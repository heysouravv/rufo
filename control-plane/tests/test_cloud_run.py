from unittest.mock import MagicMock, patch

from google.cloud import run_v2

from rufo_control_plane.gcp.cloud_run import deploy_service, resolve_image_digest


def test_already_pinned_image_passes_through_unchanged():
    image = "us-central1-docker.pkg.dev/proj/repo/agent@sha256:abc123"
    assert resolve_image_digest(image) == image


def test_tagged_image_resolves_to_digest():
    tagged = "us-central1-docker.pkg.dev/rufo-cloud-06179/rufo-agents/langgraph-real-agent:latest"
    with patch("rufo_control_plane.gcp.cloud_run.artifactregistry_v1.ArtifactRegistryClient") as MockClient:
        mock_client = MockClient.return_value
        mock_client.get_tag.return_value = MagicMock(
            version="projects/rufo-cloud-06179/locations/us-central1/repositories/rufo-agents/"
            "packages/langgraph-real-agent/versions/sha256:deadbeef"
        )
        resolved = resolve_image_digest(tagged)

    assert resolved == (
        "us-central1-docker.pkg.dev/rufo-cloud-06179/rufo-agents/langgraph-real-agent@sha256:deadbeef"
    )
    called_name = mock_client.get_tag.call_args.kwargs["name"]
    assert called_name == (
        "projects/rufo-cloud-06179/locations/us-central1/repositories/rufo-agents/"
        "packages/langgraph-real-agent/tags/latest"
    )


def test_unrecognizable_image_passes_through_unchanged():
    image = "not-a-valid-reference"
    assert resolve_image_digest(image) == image


def test_deploy_service_sets_service_account_on_template():
    with patch("rufo_control_plane.gcp.cloud_run._client") as mock_client_factory:
        mock_client = mock_client_factory.return_value
        mock_client.get_service.side_effect = Exception("not found")  # force create path
        from google.api_core.exceptions import NotFound

        mock_client.get_service.side_effect = NotFound("no such service")
        mock_operation = MagicMock()
        mock_operation.result.return_value = MagicMock(name="result", uri="https://example.run.app")
        mock_client.create_service.return_value = mock_operation

        deploy_service(
            project_id="proj",
            region="us-central1",
            service_id="svc",
            image="proj/repo/img@sha256:abc",
            port=8080,
            env={},
            labels={},
            service_account="custom-sa@proj.iam.gserviceaccount.com",
        )

        sent_service = mock_client.create_service.call_args.kwargs["service"]
        assert sent_service.template.service_account == "custom-sa@proj.iam.gserviceaccount.com"


def test_deploy_service_sets_ingress_and_invoker_iam_disabled_together():
    """A platform service (control plane, dashboard) behind the Load Balancer
    needs both settings applied in the SAME request -- setting them via
    separate gcloud calls after the fact silently resets ingress back to
    ALL, caught live twice in this exact deployment."""
    with patch("rufo_control_plane.gcp.cloud_run._client") as mock_client_factory:
        from google.api_core.exceptions import NotFound

        mock_client = mock_client_factory.return_value
        mock_client.get_service.side_effect = NotFound("no such service")
        mock_operation = MagicMock()
        mock_operation.result.return_value = MagicMock(name="result", uri="https://example.run.app")
        mock_client.create_service.return_value = mock_operation

        deploy_service(
            project_id="proj",
            region="us-central1",
            service_id="svc",
            image="proj/repo/img@sha256:abc",
            port=8080,
            env={},
            labels={},
            ingress=run_v2.IngressTraffic.INGRESS_TRAFFIC_INTERNAL_LOAD_BALANCER,
            invoker_iam_disabled=True,
        )

        sent_service = mock_client.create_service.call_args.kwargs["service"]
        assert sent_service.ingress == run_v2.IngressTraffic.INGRESS_TRAFFIC_INTERNAL_LOAD_BALANCER
        assert sent_service.invoker_iam_disabled is True


def test_deploy_service_defaults_leave_agent_services_iam_gated():
    with patch("rufo_control_plane.gcp.cloud_run._client") as mock_client_factory:
        from google.api_core.exceptions import NotFound

        mock_client = mock_client_factory.return_value
        mock_client.get_service.side_effect = NotFound("no such service")
        mock_operation = MagicMock()
        mock_operation.result.return_value = MagicMock(name="result", uri="https://example.run.app")
        mock_client.create_service.return_value = mock_operation

        deploy_service(
            project_id="proj",
            region="us-central1",
            service_id="svc",
            image="proj/repo/img@sha256:abc",
            port=8080,
            env={},
            labels={},
        )

        sent_service = mock_client.create_service.call_args.kwargs["service"]
        assert sent_service.invoker_iam_disabled is False
        assert sent_service.ingress == run_v2.IngressTraffic.INGRESS_TRAFFIC_UNSPECIFIED
