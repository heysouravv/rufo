from unittest.mock import MagicMock, patch

import pytest
from google.api_core.exceptions import FailedPrecondition
from google.cloud import compute_v1

from rufo_control_plane.gcp.load_balancer import add_path_rule


def _url_map_with_matcher(*, existing_rules=None):
    matcher = compute_v1.PathMatcher(name="api-matcher", path_rules=existing_rules or [])
    return compute_v1.UrlMap(name="rufo-cloud-urlmap", path_matchers=[matcher])


def _mock_client(url_map):
    client = MagicMock()
    client.get.return_value = url_map
    client.patch.return_value.result.return_value = None
    return client


@patch("rufo_control_plane.gcp.load_balancer.compute_v1.UrlMapsClient")
def test_add_path_rule_appends_new_rule(mock_client_cls):
    url_map = _url_map_with_matcher()
    mock_client_cls.return_value = _mock_client(url_map)

    add_path_rule(
        project_id="proj",
        url_map_name="rufo-cloud-urlmap",
        path_matcher_name="api-matcher",
        path_prefix="/agents/acme/my-agent",
        backend_service_name="rufo-abc123-my-agent",
    )

    patched_url_map = mock_client_cls.return_value.patch.call_args.kwargs["url_map_resource"]
    matcher = patched_url_map.path_matchers[0]
    assert len(matcher.path_rules) == 1
    assert list(matcher.path_rules[0].paths) == ["/agents/acme/my-agent/*"]
    assert matcher.path_rules[0].service.endswith("/backendServices/rufo-abc123-my-agent")


@patch("rufo_control_plane.gcp.load_balancer.compute_v1.UrlMapsClient")
def test_add_path_rule_leaves_other_rules_untouched(mock_client_cls):
    """The existing hand-authored /api/* rule must survive appends for
    other agents -- this is the whole reason path-based routing was chosen
    over rewriting the pathMatcher's model."""
    existing = compute_v1.PathRule(
        paths=["/api/*"],
        service="https://www.googleapis.com/compute/v1/projects/proj/global/backendServices/rufo-control-plane-backend",
    )
    url_map = _url_map_with_matcher(existing_rules=[existing])
    mock_client_cls.return_value = _mock_client(url_map)

    add_path_rule(
        project_id="proj",
        url_map_name="rufo-cloud-urlmap",
        path_matcher_name="api-matcher",
        path_prefix="/agents/acme/my-agent",
        backend_service_name="rufo-abc123-my-agent",
    )

    patched_url_map = mock_client_cls.return_value.patch.call_args.kwargs["url_map_resource"]
    matcher = patched_url_map.path_matchers[0]
    assert len(matcher.path_rules) == 2
    assert list(matcher.path_rules[0].paths) == ["/api/*"]
    assert list(matcher.path_rules[1].paths) == ["/agents/acme/my-agent/*"]


@patch("rufo_control_plane.gcp.load_balancer.compute_v1.UrlMapsClient")
def test_add_path_rule_replaces_existing_rule_for_same_agent(mock_client_cls):
    """Redeploying the same agent updates its rule in place instead of
    duplicating it."""
    stale = compute_v1.PathRule(
        paths=["/agents/acme/my-agent/*"],
        service="https://www.googleapis.com/compute/v1/projects/proj/global/backendServices/old-backend",
    )
    url_map = _url_map_with_matcher(existing_rules=[stale])
    mock_client_cls.return_value = _mock_client(url_map)

    add_path_rule(
        project_id="proj",
        url_map_name="rufo-cloud-urlmap",
        path_matcher_name="api-matcher",
        path_prefix="/agents/acme/my-agent",
        backend_service_name="new-backend",
    )

    patched_url_map = mock_client_cls.return_value.patch.call_args.kwargs["url_map_resource"]
    matcher = patched_url_map.path_matchers[0]
    assert len(matcher.path_rules) == 1
    assert matcher.path_rules[0].service.endswith("/backendServices/new-backend")


@patch("rufo_control_plane.gcp.load_balancer.compute_v1.UrlMapsClient")
def test_add_path_rule_raises_when_matcher_missing(mock_client_cls):
    url_map = compute_v1.UrlMap(name="rufo-cloud-urlmap", path_matchers=[])
    mock_client_cls.return_value = _mock_client(url_map)

    with pytest.raises(RuntimeError, match="api-matcher"):
        add_path_rule(
            project_id="proj",
            url_map_name="rufo-cloud-urlmap",
            path_matcher_name="api-matcher",
            path_prefix="/agents/acme/my-agent",
            backend_service_name="rufo-abc123-my-agent",
        )


@patch("rufo_control_plane.gcp.load_balancer.time.sleep", return_value=None)
@patch("rufo_control_plane.gcp.load_balancer.compute_v1.UrlMapsClient")
def test_add_path_rule_retries_on_conflicting_concurrent_update(mock_client_cls, mock_sleep):
    url_map = _url_map_with_matcher()
    client = _mock_client(url_map)
    client.patch.side_effect = [FailedPrecondition("conflict"), MagicMock(result=lambda: None)]
    mock_client_cls.return_value = client

    add_path_rule(
        project_id="proj",
        url_map_name="rufo-cloud-urlmap",
        path_matcher_name="api-matcher",
        path_prefix="/agents/acme/my-agent",
        backend_service_name="rufo-abc123-my-agent",
    )

    assert client.patch.call_count == 2
