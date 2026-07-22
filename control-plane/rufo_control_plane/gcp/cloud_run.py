from __future__ import annotations

from dataclasses import dataclass

from google.api_core.exceptions import NotFound
from google.cloud import run_v2


@dataclass(frozen=True)
class CloudRunDeployResult:
    service_name: str  # fully-qualified: projects/P/locations/L/services/S
    uri: str | None


def _client() -> run_v2.ServicesClient:
    return run_v2.ServicesClient()


def _service_path(project_id: str, region: str, service_id: str) -> str:
    return f"projects/{project_id}/locations/{region}/services/{service_id}"


def deploy_service(
    *,
    project_id: str,
    region: str,
    service_id: str,
    image: str,
    port: int,
    env: dict[str, str],
    labels: dict[str, str],
    min_instances: int = 0,
    max_instances: int = 5,
    invoker_members: list[str] | None = None,
) -> CloudRunDeployResult:
    """Create or update a Cloud Run service. Idempotent: re-running with the
    same service_id updates the existing service (a new revision) instead of
    failing on 'already exists'.

    Services are deployed *without* public (`allUsers`) access -- the
    `eldridgemorgan.com` org enforces a domain-restricted-sharing policy that
    rejects that binding outright, and it's the right default anyway: the
    control plane should be the sole authenticated caller into each
    customer's Cloud Run service (its own gateway/proxy), not the raw
    internet. Pass `invoker_members` (e.g. the control plane's own service
    account) to grant specific callers `roles/run.invoker`.
    """
    client = _client()
    parent = f"projects/{project_id}/locations/{region}"

    container = run_v2.Container(
        image=image,
        ports=[run_v2.ContainerPort(container_port=port)],
        env=[run_v2.EnvVar(name=k, value=v) for k, v in env.items()],
    )
    template = run_v2.RevisionTemplate(
        containers=[container],
        scaling=run_v2.RevisionScaling(
            min_instance_count=min_instances, max_instance_count=max_instances
        ),
    )
    service = run_v2.Service(template=template, labels=labels)

    try:
        existing = client.get_service(name=_service_path(project_id, region, service_id))
        service.name = existing.name
        operation = client.update_service(service=service)
    except NotFound:
        operation = client.create_service(parent=parent, service=service, service_id=service_id)

    result = operation.result()  # blocks until the revision is ready or fails

    if invoker_members:
        _grant_invokers(project_id, region, service_id, invoker_members)

    return CloudRunDeployResult(service_name=result.name, uri=result.uri)


def _grant_invokers(project_id: str, region: str, service_id: str, members: list[str]) -> None:
    client = _client()
    resource = _service_path(project_id, region, service_id)
    policy = client.get_iam_policy(request={"resource": resource})
    policy.bindings.add(role="roles/run.invoker", members=members)
    client.set_iam_policy(request={"resource": resource, "policy": policy})


def delete_service(*, project_id: str, region: str, service_id: str) -> None:
    client = _client()
    try:
        client.delete_service(name=_service_path(project_id, region, service_id)).result()
    except NotFound:
        pass


def get_service(*, project_id: str, region: str, service_id: str) -> run_v2.Service | None:
    client = _client()
    try:
        return client.get_service(name=_service_path(project_id, region, service_id))
    except NotFound:
        return None
