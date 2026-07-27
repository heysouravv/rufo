from rufo_control_plane.build.dockerfile import synthesize_dockerfile
from rufo_core.manifest import AgentManifest


def _manifest(**overrides) -> AgentManifest:
    defaults = dict(
        name="demo-agent",
        entrypoint_module="agent.py",
        entrypoint_attr="agent",
        runtime="python@3.11",
        dependencies={"langgraph": "^0.2", "langchain-openai": "0.2.1"},
        policy_file="policy.yaml",
    )
    defaults.update(overrides)
    return AgentManifest(**defaults)


def test_dockerfile_installs_published_rufo_packages():
    dockerfile = synthesize_dockerfile(_manifest(), has_deploy_config=True)
    assert 'git+https://github.com/heysouravv/rufo.git#subdirectory=packages/rufo-core' in dockerfile
    assert 'git+https://github.com/heysouravv/rufo.git#subdirectory=packages/rufo-sdk' in dockerfile
    assert 'git+https://github.com/heysouravv/rufo.git#subdirectory=packages/rufo-runtime' in dockerfile


def test_dockerfile_converts_caret_and_bare_versions_to_pip_specs():
    dockerfile = synthesize_dockerfile(_manifest(), has_deploy_config=False)
    assert '"langgraph>=0.2"' in dockerfile
    assert '"langchain-openai>=0.2.1"' in dockerfile


def test_dockerfile_passes_through_pip_native_operators():
    manifest = _manifest(dependencies={"langgraph": "==0.2.5"})
    dockerfile = synthesize_dockerfile(manifest, has_deploy_config=False)
    assert '"langgraph==0.2.5"' in dockerfile


def test_dockerfile_sets_entrypoint_env_vars():
    dockerfile = synthesize_dockerfile(_manifest(), has_deploy_config=True)
    assert "ENV RUFO_AGENT_MODULE=/app/agent.py" in dockerfile
    assert "ENV RUFO_AGENT_ENTRYPOINT=agent" in dockerfile
    assert "ENV RUFO_POLICY_PATH=/app/policy.yaml" in dockerfile
    assert "ENV RUFO_DEPLOY_CONFIG_PATH=/app/rufo.yaml" in dockerfile


def test_dockerfile_omits_deploy_config_env_when_absent():
    dockerfile = synthesize_dockerfile(_manifest(), has_deploy_config=False)
    assert "RUFO_DEPLOY_CONFIG_PATH" not in dockerfile


def test_dockerfile_copies_source_and_exposes_port():
    dockerfile = synthesize_dockerfile(_manifest(), has_deploy_config=False)
    assert "COPY . ." in dockerfile
    assert "EXPOSE 8080" in dockerfile
    assert "uvicorn rufo_runtime.app:app" in dockerfile


def test_dockerfile_installs_git_before_pip_install():
    # python:3.11-slim has no git binary -- pip can't resolve git+https
    # dependencies without it, a real failure caught by a live Cloud Build run.
    dockerfile = synthesize_dockerfile(_manifest(), has_deploy_config=False)
    assert "apt-get install -y --no-install-recommends git" in dockerfile
    assert dockerfile.index("apt-get install") < dockerfile.index("pip install")
