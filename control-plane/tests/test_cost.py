import pytest
from rufo_control_plane.cost import estimate_cost, parse_cpu, parse_memory_gib


def test_parse_cpu_millicores():
    assert parse_cpu("1000m") == 1.0
    assert parse_cpu("500m") == 0.5


def test_parse_cpu_whole_cores():
    assert parse_cpu("2") == 2.0


def test_parse_memory_variants():
    assert parse_memory_gib("512Mi") == pytest.approx(0.5, rel=1e-6)
    assert parse_memory_gib("2Gi") == 2.0
    assert parse_memory_gib("1048576Ki") == pytest.approx(1.0, rel=1e-6)


def test_estimate_cost_matches_real_agent_shape():
    # 1 vCPU / 512Mi, ~66 real billed seconds observed from a live Cloud Run
    # deployment -- sanity-checks the formula against real-world scale.
    cost = estimate_cost(billable_instance_seconds=66.12, vcpu_count=1.0, memory_gib=0.5)
    assert cost.estimated_cost_usd == pytest.approx(66.12 * (0.00012 + 0.5 * 0.0000125), rel=1e-6)
    assert cost.estimated_cost_usd < 0.01  # trivially cheap at this scale, as modeled


def test_estimate_cost_zero_usage_is_free():
    cost = estimate_cost(billable_instance_seconds=0.0, vcpu_count=1.0, memory_gib=0.5)
    assert cost.estimated_cost_usd == 0.0
