from __future__ import annotations

import re
from dataclasses import dataclass

from google.api_core.exceptions import NotFound
from google.cloud import artifactregistry_v1, run_v2

_TAGGED_IMAGE_RE = re.compile(
    r"^(?P<host>[^/]+)/(?P<project>[^/]+)/(?P<repo>[^/]+)/(?P<pkg>.+):(?P<tag>[^:/]+)$"
)


@dataclass(frozen=True)
class CloudRunDeployResult:
    service_name: str  # fully-qualified: projects/P/locations/L/services/S
    uri: str | None


def _client() -> run_v2.ServicesClient:
    return run_v2.ServicesClient()


def _service_path(project_id: str, region: str, service_id: str) -> str:
    return f"projects/{project_id}/locations/{region}/services/{service_id}"


def resolve_image_digest(image: str) -> str:
    """Resolve a mutable `...:tag` reference to an immutable `...@sha256:...`
    one. Deploying by a mutable tag proved unreliable in practice: a real
    live test re-deployed to the same tag right after pushing a fix, and
    Cloud Run served a stale cached digest for that tag instead of the new
    one -- silently running old, broken code. Already-pinned references
    (containing `@sha256:`) pass through unchanged.
    """
    if "@sha256:" in image:
        return image

    match = _TAGGED_IMAGE_RE.match(image)
    if not match:
        return image  # not a recognizable Artifact Registry tag -- best effort

    host, project, repo, pkg, tag = match.groups()
    location = host.split("-docker.pkg.dev")[0]

    client = artifactregistry_v1.ArtifactRegistryClient()
    tag_name = f"projects/{project}/locations/{location}/repositories/{repo}/packages/{pkg}/tags/{tag}"
    resolved_tag = client.get_tag(name=tag_name)
    digest = resolved_tag.version.rsplit("/", 1)[-1]  # ".../versions/sha256:..."
    return f"{host}/{project}/{repo}/{pkg}@{digest}"


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
    service_account: str | None = None,
    ingress: run_v2.IngressTraffic | None = None,
    invoker_iam_disabled: bool = False,
    cloudsql_instances: list[str] | None = None,
) -> CloudRunDeployResult:
    """Create or update a Cloud Run service. Idempotent: re-running with the
    same service_id updates the existing service (a new revision) instead of
    failing on 'already exists'.

    Customer *agent* services (the default: `ingress=None` -> Cloud Run's
    default INGRESS_TRAFFIC_ALL, `invoker_iam_disabled=False`) are deployed
    *without* public (`allUsers`) access -- the `eldridgemorgan.com` org's
    domain-restricted-sharing policy rejects that binding outright, and it's
    the right default anyway: the control plane should be the sole
    authenticated caller into each customer's service, not the raw internet.
    Pass `invoker_members` to grant specific callers `roles/run.invoker`.

    Rufo's own *platform* services (control plane, dashboard) sitting behind
    the public Load Balancer need the opposite: `ingress=
    INGRESS_TRAFFIC_INTERNAL_LOAD_BALANCER` (blocks direct .run.app access at
    the network layer) plus `invoker_iam_disabled=True` (lets the load
    balancer's serverless-NEG integration reach the service at all --
    ingress alone wasn't sufficient in practice; a real 403 from Google
    Frontend confirmed the IAM invoker check still applies independently of
    the ingress restriction). Both must be set through this one call: doing
    it via separate `gcloud services update` commands after the fact is
    fragile since each subsequent update to unrelated fields (a redeploy, a
    service-account change) silently resets ingress back to ALL unless it's
    included in the same request -- caught live, twice, in this exact repo.

    `cloudsql_instances` (e.g. ["project:region:instance"]) mounts a Unix
    socket at /cloudsql/<instance> inside the container -- the standard way
    for a Cloud Run workload to reach Cloud SQL with a driver that has no
    dedicated connector support (psycopg3, needed by LangGraph's
    PostgresSaver). No VPC or Cloud SQL Auth Proxy sidecar required.
    """
    client = _client()
    parent = f"projects/{project_id}/locations/{region}"
    image = resolve_image_digest(image)

    container = run_v2.Container(
        image=image,
        ports=[run_v2.ContainerPort(container_port=port)],
        env=[run_v2.EnvVar(name=k, value=v) for k, v in env.items()],
    )
    template_kwargs = {}
    if cloudsql_instances:
        template_kwargs["annotations"] = {
            "run.googleapis.com/cloudsql-instances": ",".join(cloudsql_instances)
        }
    template = run_v2.RevisionTemplate(
        containers=[container],
        scaling=run_v2.RevisionScaling(
            min_instance_count=min_instances, max_instance_count=max_instances
        ),
        service_account=service_account or None,
        **template_kwargs,
    )
    service = run_v2.Service(
        template=template,
        labels=labels,
        invoker_iam_disabled=invoker_iam_disabled,
        **({"ingress": ingress} if ingress is not None else {}),
    )

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
