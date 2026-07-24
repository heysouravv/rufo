from __future__ import annotations

import io
import tarfile

from google.cloud import storage
from google.cloud.devtools import cloudbuild_v1

STAGING_BUCKET_SUFFIX = "rufo-agent-sources"
BUILD_TIMEOUT_SECONDS = 900


def _bucket_name(project_id: str) -> str:
    return f"{project_id}-{STAGING_BUCKET_SUFFIX}"


def ensure_staging_bucket(project_id: str, region: str) -> str:
    client = storage.Client(project=project_id)
    bucket_name = _bucket_name(project_id)
    if not client.bucket(bucket_name).exists():
        client.create_bucket(bucket_name, location=region)
    return bucket_name


def inject_dockerfile(source_tar_gz: bytes, dockerfile_content: str) -> bytes:
    """Re-pack an uploaded source tarball with a synthesized Dockerfile added
    at its root -- customers upload rufo.toml/rufo.yaml/agent code only, a
    Dockerfile of their own (if present) is replaced rather than layered."""
    src = tarfile.open(fileobj=io.BytesIO(source_tar_gz), mode="r:gz")
    out_buf = io.BytesIO()
    with tarfile.open(fileobj=out_buf, mode="w:gz") as out:
        for member in src.getmembers():
            if member.name.lstrip("./") == "Dockerfile":
                continue
            extracted = src.extractfile(member) if member.isfile() else None
            out.addfile(member, extracted)

        dockerfile_bytes = dockerfile_content.encode()
        info = tarfile.TarInfo(name="Dockerfile")
        info.size = len(dockerfile_bytes)
        out.addfile(info, io.BytesIO(dockerfile_bytes))
    return out_buf.getvalue()


def upload_source(project_id: str, bucket_name: str, org_fingerprint: str, build_id: str, tar_gz: bytes) -> str:
    """Returns the GCS object path (bucket-relative) the source was written to."""
    client = storage.Client(project=project_id)
    object_path = f"{org_fingerprint}/{build_id}.tar.gz"
    client.bucket(bucket_name).blob(object_path).upload_from_string(tar_gz, content_type="application/gzip")
    return object_path


def submit_build_and_wait(
    *,
    project_id: str,
    bucket_name: str,
    object_path: str,
    image_tag: str,
    timeout_seconds: int = BUILD_TIMEOUT_SECONDS,
) -> None:
    """Submits a Cloud Build job that docker-builds the uploaded source (with
    its synthesized Dockerfile) and pushes the result to Artifact Registry,
    blocking until it finishes. Raises RuntimeError on build failure."""
    client = cloudbuild_v1.CloudBuildClient()
    build = cloudbuild_v1.Build(
        source=cloudbuild_v1.Source(
            storage_source=cloudbuild_v1.StorageSource(bucket=bucket_name, object_=object_path)
        ),
        steps=[
            cloudbuild_v1.BuildStep(
                name="gcr.io/cloud-builders/docker",
                args=["build", "-t", image_tag, "."],
            )
        ],
        images=[image_tag],
        timeout={"seconds": timeout_seconds},
    )
    operation = client.create_build(project_id=project_id, build=build)
    result = operation.result(timeout=timeout_seconds + 60)
    if result.status != cloudbuild_v1.Build.Status.SUCCESS:
        raise RuntimeError(f"build did not succeed (status={result.status.name}): {result.log_url}")
