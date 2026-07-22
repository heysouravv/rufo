from rufo_control_plane.routes.deployments import _service_id_for


def test_service_id_under_50_chars_for_real_clerk_org_id():
    # Real-shaped Clerk org_id (from the live e2e test) -- this exact input
    # previously produced a 51-char service_id that Cloud Run rejected.
    org_id = "org_3GqnEErDve039Vou9lukjy18peK"
    service_id = _service_id_for(org_id, "e2e-test-agent")
    assert len(service_id) < 50
    assert service_id.startswith("rufo-")
    assert not service_id.endswith("-")


def test_service_id_deterministic_for_same_org_and_agent():
    a = _service_id_for("org_abc123", "my-agent")
    b = _service_id_for("org_abc123", "my-agent")
    assert a == b


def test_service_id_differs_across_orgs():
    a = _service_id_for("org_abc123", "my-agent")
    b = _service_id_for("org_xyz789", "my-agent")
    assert a != b


def test_service_id_handles_long_agent_name():
    service_id = _service_id_for("org_abc123", "a" * 100)
    assert len(service_id) < 50
    assert not service_id.endswith("-")
