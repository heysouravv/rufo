from __future__ import annotations

from dataclasses import dataclass

# Customer-facing rates: ~5x markup over Cloud Run's published Tier 1 rate
# ($0.000024/vCPU-sec, $0.0000025/GiB-sec) -- see the pricing stress-test.
# Compute is priced for legibility/light margin, not as the business; the
# seat/governance tier is the actual revenue line.
CPU_RATE_PER_VCPU_SECOND = 0.00012
MEMORY_RATE_PER_GIB_SECOND = 0.0000125


@dataclass(frozen=True)
class CostEstimate:
    billable_instance_seconds: float
    vcpu_count: float
    memory_gib: float
    estimated_cost_usd: float


def parse_cpu(cpu_str: str) -> float:
    """Kubernetes-style CPU quantity ('1000m', '2', '500m') -> vCPU count."""
    if cpu_str.endswith("m"):
        return float(cpu_str[:-1]) / 1000.0
    return float(cpu_str)


def parse_memory_gib(mem_str: str) -> float:
    """Kubernetes-style memory quantity ('512Mi', '2Gi', '256Ki') -> GiB."""
    if mem_str.endswith("Gi"):
        return float(mem_str[:-2])
    if mem_str.endswith("Mi"):
        return float(mem_str[:-2]) / 1024.0
    if mem_str.endswith("Ki"):
        return float(mem_str[:-2]) / (1024.0 * 1024.0)
    return float(mem_str) / (1024.0**3)  # assume raw bytes


def estimate_cost(billable_instance_seconds: float, vcpu_count: float, memory_gib: float) -> CostEstimate:
    cost = billable_instance_seconds * (
        vcpu_count * CPU_RATE_PER_VCPU_SECOND + memory_gib * MEMORY_RATE_PER_GIB_SECOND
    )
    return CostEstimate(
        billable_instance_seconds=billable_instance_seconds,
        vcpu_count=vcpu_count,
        memory_gib=memory_gib,
        estimated_cost_usd=cost,
    )
