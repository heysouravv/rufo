from __future__ import annotations

import io
import os
import tarfile
import tomllib
import uuid

import yaml
from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from rufo_core.deploy_config import parse_deploy_config
from rufo_core.errors import PolicyLoadError
from rufo_core.manifest import parse_manifest

from rufo_control_plane.auth import AuthContext
from rufo_control_plane.build.dockerfile import synthesize_dockerfile
from rufo_control_plane.gcp.cloud_run import deploy_service
from rufo_control_plane.gcp.source_build import (
    ensure_staging_bucket,
    inject_dockerfile,
    submit_build_and_wait,
    upload_source,
)
from rufo_control_plane.routes.deployments import _org_fingerprint, _sanitize, _service_id_for, infra_env_for
from rufo_control_plane.settings import Settings
from rufo_control_plane.store import ControlPlaneStore


def _read_tar_member(tar: tarfile.TarFile, name: str) -> bytes | None:
    for candidate in (name, f"./{name}"):
        try:
            member = tar.getmember(candidate)
        except KeyError:
            continue
        extracted = tar.extractfile(member)
        if extracted is not None:
            return extracted.read()
    return None


def build_router(settings: Settings, store: ControlPlaneStore, auth_dependency) -> APIRouter:
    router = APIRouter(prefix="/deployments", tags=["deployments"])

    @router.post("/from-source")
    async def deploy_from_source(
        source: UploadFile = File(..., description="tar.gz of the agent directory: rufo.toml, rufo.yaml, policy.yaml, agent code"),
        auth: AuthContext = Depends(auth_dependency),
    ) -> dict:
        tar_bytes = await source.read()
        try:
            tar = tarfile.open(fileobj=io.BytesIO(tar_bytes), mode="r:gz")
        except tarfile.TarError as exc:
            raise HTTPException(status_code=400, detail=f"not a valid .tar.gz upload: {exc}") from exc

        manifest_bytes = _read_tar_member(tar, "rufo.toml")
        if manifest_bytes is None:
            raise HTTPException(status_code=400, detail="uploaded source is missing rufo.toml")
        try:
            manifest = parse_manifest(tomllib.loads(manifest_bytes.decode()))
        except PolicyLoadError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

        deploy_config_bytes = _read_tar_member(tar, "rufo.yaml")
        deploy_config = parse_deploy_config(yaml.safe_load(deploy_config_bytes) if deploy_config_bytes else {})

        # Resolve secrets before kicking off an expensive Cloud Build job --
        # no point spending minutes building an image we can't deploy.
        env: dict[str, str] = {}
        for name in deploy_config.env:
            value = store.get_secret(auth.org_id, name) or os.environ.get(name)
            if not value:
                raise HTTPException(
                    status_code=400,
                    detail=f"no value for secret '{name}' -- set it with POST /api/secrets first",
                )
            env[name] = value

        infra_env, cloudsql_instances = infra_env_for(settings)
        env.update(infra_env)

        build_id = uuid.uuid4().hex[:12]
        dockerfile_content = synthesize_dockerfile(manifest, has_deploy_config=deploy_config_bytes is not None)
        packed = inject_dockerfile(tar_bytes, dockerfile_content)

        bucket_name = ensure_staging_bucket(settings.gcp_project_id, settings.gcp_region)
        object_path = upload_source(settings.gcp_project_id, bucket_name, _org_fingerprint(auth.org_id), build_id, packed)

        service_id = _service_id_for(auth.org_id, manifest.name)
        image_tag = (
            f"{settings.gcp_region}-docker.pkg.dev/{settings.gcp_project_id}/"
            f"{settings.artifact_registry_repo}/{service_id}:{build_id}"
        )

        try:
            submit_build_and_wait(
                project_id=settings.gcp_project_id,
                bucket_name=bucket_name,
                object_path=object_path,
                image_tag=image_tag,
            )
        except RuntimeError as exc:
            raise HTTPException(status_code=502, detail=f"build failed: {exc}") from exc

        try:
            result = deploy_service(
                project_id=settings.gcp_project_id,
                region=settings.gcp_region,
                service_id=service_id,
                image=image_tag,
                port=deploy_config.port,
                env=env,
                labels={
                    "app": "rufo",
                    "managed-by": "rufo-control-plane",
                    "org": _org_fingerprint(auth.org_id),
                    "agent": _sanitize(manifest.name, max_len=63) or "agent",
                },
                min_instances=deploy_config.scaling.min_instances,
                max_instances=deploy_config.scaling.max_instances,
                cloudsql_instances=cloudsql_instances,
            )
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(status_code=502, detail=f"Cloud Run deploy failed: {exc}") from exc

        return store.upsert_deployment(
            org_id=auth.org_id,
            agent_name=manifest.name,
            service_id=service_id,
            region=settings.gcp_region,
            image=image_tag,
            uri=result.uri,
            status="running",
        )

    return router
