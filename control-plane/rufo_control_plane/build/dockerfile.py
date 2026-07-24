from __future__ import annotations

from rufo_core.manifest import AgentManifest

RUFO_REPO_URL = "https://github.com/heysouravv/rufo.git"

_RUFO_PACKAGES = ["rufo-core", "rufo-sdk", "rufo-runtime"]


def _pip_spec(name: str, version: str) -> str:
    """Poetry-style manifest versions ("^0.2", "~1.4", bare "2.0") aren't all
    valid pip specs -- pip has no caret operator. A bare or caret-prefixed
    version means "at least this" in practice for an MVP build, so both
    collapse to `>=`; anything already using a pip-compatible operator
    (==, >=, <=, ~=) passes through unchanged.
    """
    version = version.strip()
    if version.startswith(("==", ">=", "<=", "~=", ">", "<")):
        return f"{name}{version}"
    if version.startswith("^"):
        return f"{name}>={version[1:]}"
    return f"{name}>={version}"


def synthesize_dockerfile(manifest: AgentManifest, *, has_deploy_config: bool, repo_url: str = RUFO_REPO_URL) -> str:
    """Build a Dockerfile for an uploaded agent source tree, without requiring
    the customer to write one. Installs the three published Rufo packages
    straight from GitHub (git+https, per the public-repo distribution model)
    plus whatever the agent's own rufo.toml [dependencies] declare, then
    copies the uploaded source and points the runtime at the agent's entrypoint.
    """
    rufo_installs = " \\\n    ".join(f'"git+{repo_url}#subdirectory=packages/{pkg}"' for pkg in _RUFO_PACKAGES)
    agent_installs = " \\\n    ".join(f'"{_pip_spec(name, version)}"' for name, version in manifest.dependencies.items())
    pip_install_lines = rufo_installs + (" \\\n    " + agent_installs if agent_installs else "")

    entrypoint_module = manifest.entrypoint_module
    if not entrypoint_module.startswith("/"):
        entrypoint_module = f"/app/{entrypoint_module}"
    policy_file = manifest.policy_file
    if not policy_file.startswith("/"):
        policy_file = f"/app/{policy_file}"

    deploy_config_env = "ENV RUFO_DEPLOY_CONFIG_PATH=/app/rufo.yaml\n" if has_deploy_config else ""

    return f"""FROM python:3.11-slim
WORKDIR /app
RUN pip install --no-cache-dir \\
    {pip_install_lines}
COPY . .
ENV RUFO_POLICY_PATH={policy_file}
ENV RUFO_AGENT_MODULE={entrypoint_module}
ENV RUFO_AGENT_ENTRYPOINT={manifest.entrypoint_attr}
{deploy_config_env}ENV RUFO_DB_PATH=/app/rufo_data/rufo.db
EXPOSE 8080
CMD ["sh", "-c", "uvicorn rufo_runtime.app:app --host 0.0.0.0 --port ${{PORT:-8080}}"]
"""
