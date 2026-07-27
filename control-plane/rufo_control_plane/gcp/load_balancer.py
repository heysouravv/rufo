from __future__ import annotations

import time

from google.api_core.exceptions import Aborted, FailedPrecondition, NotFound
from google.cloud import compute_v1


def ensure_serverless_neg(project_id: str, region: str, service_id: str) -> str:
    """Idempotent: creates (if missing) a regional serverless NEG pointing at
    the given Cloud Run service and returns its self_link. Reuses service_id
    as the NEG's name -- already unique per (org, agent), already under
    Cloud Run's 49-char service-id limit."""
    client = compute_v1.RegionNetworkEndpointGroupsClient()
    try:
        neg = client.get(project=project_id, region=region, network_endpoint_group=service_id)
        return neg.self_link
    except NotFound:
        pass

    neg_resource = compute_v1.NetworkEndpointGroup(
        name=service_id,
        network_endpoint_type=compute_v1.NetworkEndpointGroup.NetworkEndpointType.SERVERLESS.name,
        cloud_run=compute_v1.NetworkEndpointGroupCloudRun(service=service_id),
    )
    operation = client.insert(project=project_id, region=region, network_endpoint_group_resource=neg_resource)
    operation.result()
    return client.get(project=project_id, region=region, network_endpoint_group=service_id).self_link


def ensure_backend_service(project_id: str, service_id: str, neg_self_link: str) -> str:
    """Idempotent: creates (if missing) a global backend service backed by
    the given serverless NEG, mirroring the platform services' own backend
    config (EXTERNAL_MANAGED, HTTP/80) so it plugs into the same Global
    External HTTPS Load Balancer. Returns the backend service name."""
    client = compute_v1.BackendServicesClient()
    try:
        client.get(project=project_id, backend_service=service_id)
        return service_id
    except NotFound:
        pass

    backend_service = compute_v1.BackendService(
        name=service_id,
        load_balancing_scheme=compute_v1.BackendService.LoadBalancingScheme.EXTERNAL_MANAGED.name,
        protocol=compute_v1.BackendService.Protocol.HTTP.name,
        port=80,
        port_name="http",
        timeout_sec=30,
        backends=[compute_v1.Backend(group=neg_self_link, balancing_mode=compute_v1.Backend.BalancingMode.UTILIZATION.name)],
    )
    operation = client.insert(project=project_id, backend_service_resource=backend_service)
    operation.result()
    return service_id


def add_path_rule(
    *,
    project_id: str,
    url_map_name: str,
    path_matcher_name: str,
    path_prefix: str,
    backend_service_name: str,
    max_retries: int = 3,
) -> None:
    """Read-modify-write the URL map's path matcher: replaces the rule if one
    with the same `paths` already exists (redeploy of the same agent), else
    appends a new one -- same shape as the existing hand-authored `/api/*`
    rule, just one more entry per deployed agent.

    Retries on a conflicting concurrent update (a second deploy patching the
    same URL map at the same time is rare but real) rather than failing the
    whole deploy outright.
    """
    client = compute_v1.UrlMapsClient()
    backend_service_url = (
        f"https://www.googleapis.com/compute/v1/projects/{project_id}/global/backendServices/{backend_service_name}"
    )
    paths = [f"{path_prefix}/*"]

    for attempt in range(max_retries):
        url_map = client.get(project=project_id, url_map=url_map_name)
        matcher = next((m for m in url_map.path_matchers if m.name == path_matcher_name), None)
        if matcher is None:
            raise RuntimeError(f"path matcher {path_matcher_name!r} not found on URL map {url_map_name!r}")

        existing_rule = next((r for r in matcher.path_rules if list(r.paths) == paths), None)
        if existing_rule is not None:
            existing_rule.service = backend_service_url
        else:
            matcher.path_rules.append(compute_v1.PathRule(paths=paths, service=backend_service_url))

        try:
            operation = client.patch(project=project_id, url_map=url_map_name, url_map_resource=url_map)
            operation.result()
            return
        except (Aborted, FailedPrecondition):
            if attempt == max_retries - 1:
                raise
            time.sleep(1 + attempt)
