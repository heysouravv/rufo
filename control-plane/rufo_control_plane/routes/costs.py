from __future__ import annotations

from dataclasses import asdict

from fastapi import APIRouter, Depends

from rufo_control_plane.auth import AuthContext
from rufo_control_plane.cost import estimate_cost, parse_cpu, parse_memory_gib
from rufo_control_plane.gcp.cloud_run import get_service
from rufo_control_plane.gcp.monitoring import get_billable_instance_seconds
from rufo_control_plane.settings import Settings
from rufo_control_plane.store import ControlPlaneStore


def build_router(settings: Settings, store: ControlPlaneStore, auth_dependency) -> APIRouter:
    router = APIRouter(prefix="/costs", tags=["costs"])

    @router.get("")
    def get_costs(window_hours: float = 24 * 30, auth: AuthContext = Depends(auth_dependency)) -> list[dict]:
        """Per-agent cost breakdown for the caller's org, using Cloud Run's own
        billable_instance_time metric (the same one Google bills on) rather
        than an estimate derived from measured request duration.
        """
        results = []
        for deployment in store.list_deployments(auth.org_id):
            service = get_service(
                project_id=settings.gcp_project_id,
                region=settings.gcp_region,
                service_id=deployment["service_id"],
            )
            if service is None:
                continue  # deployed in our store but no longer exists in Cloud Run

            limits = dict(service.template.containers[0].resources.limits)
            vcpu_count = parse_cpu(limits.get("cpu", "1000m"))
            memory_gib = parse_memory_gib(limits.get("memory", "512Mi"))

            usage = get_billable_instance_seconds(
                settings.gcp_project_id, deployment["service_id"], window_hours
            )
            cost = estimate_cost(usage.billable_instance_seconds, vcpu_count, memory_gib)

            results.append(
                {
                    "agent_name": deployment["agent_name"],
                    "service_id": deployment["service_id"],
                    "window_hours": window_hours,
                    **asdict(cost),
                }
            )
        return results

    return router
