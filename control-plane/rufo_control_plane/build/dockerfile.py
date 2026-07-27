from __future__ import annotations

import re

from rufo_core.manifest import AgentManifest

RUFO_REPO_URL = "https://github.com/heysouravv/rufo.git"

_RUFO_PACKAGES = ["rufo-core", "rufo-sdk", "rufo-runtime"]

# Every one of these fields comes straight from an uploaded rufo.toml --
# untrusted input from whichever org submitted it. Without an allow-list,
# a crafted dependency name/version (TOML keys/values can contain arbitrary
# characters) can break out of the quoted pip install argument list and
# inject arbitrary Dockerfile instructions, executed by Cloud Build under
# the control plane's own service account. Caught in a pre-UAT security
# audit, not live-exploited.
_PACKAGE_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*(\[[A-Za-z0-9,_-]+\])?$")
_VERSION_SPEC_RE = re.compile(r"^[A-Za-z0-9.*_+!<>=,~^ -]+$")
_MODULE_PATH_RE = re.compile(r"^[A-Za-z0-9_./-]+$")
_PY_IDENTIFIER_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


class UnsafeManifestError(ValueError):
    """Raised when a rufo.toml field contains characters that could break
    out of the synthesized Dockerfile's quoting -- rejected outright rather
    than sanitized, since a build-time injection is a real code-execution
    risk, not a cosmetic issue."""


def _require_match(pattern: re.Pattern, value: str, field: str) -> str:
    if not pattern.match(value):
        raise UnsafeManifestError(f"{field} contains characters that aren't allowed: {value!r}")
    return value


def _require_safe_path(value: str, field: str) -> str:
    _require_match(_MODULE_PATH_RE, value, field)
    if any(part == ".." for part in value.split("/")):
        raise UnsafeManifestError(f"{field} may not contain '..' path segments: {value!r}")
    return value


def _pip_spec(name: str, version: str) -> str:
    """Poetry-style manifest versions ("^0.2", "~1.4", bare "2.0") aren't all
    valid pip specs -- pip has no caret operator. A bare or caret-prefixed
    version means "at least this" in practice for an MVP build, so both
    collapse to `>=`; anything already using a pip-compatible operator
    (==, >=, <=, ~=) passes through unchanged.
    """
    _require_match(_PACKAGE_NAME_RE, name, "dependency name")
    version = version.strip()
    _require_match(_VERSION_SPEC_RE, version, f"version for {name!r}")
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

    Raises UnsafeManifestError if any manifest field contains characters
    that could break out of the Dockerfile's quoting -- callers should map
    that to a 400, not let it crash as an unhandled 500.
    """
    rufo_installs = " \\\n    ".join(f'"git+{repo_url}#subdirectory=packages/{pkg}"' for pkg in _RUFO_PACKAGES)
    agent_installs = " \\\n    ".join(f'"{_pip_spec(name, version)}"' for name, version in manifest.dependencies.items())
    pip_install_lines = rufo_installs + (" \\\n    " + agent_installs if agent_installs else "")

    entrypoint_module = _require_safe_path(manifest.entrypoint_module, "agent.entrypoint module path")
    if not entrypoint_module.startswith("/"):
        entrypoint_module = f"/app/{entrypoint_module}"
    policy_file = _require_safe_path(manifest.policy_file, "policy.file")
    if not policy_file.startswith("/"):
        policy_file = f"/app/{policy_file}"
    _require_match(_PY_IDENTIFIER_RE, manifest.entrypoint_attr, "agent.entrypoint attribute name")

    deploy_config_env = "ENV RUFO_DEPLOY_CONFIG_PATH=/app/rufo.yaml\n" if has_deploy_config else ""

    return f"""FROM python:3.11-slim
WORKDIR /app
RUN apt-get update && apt-get install -y --no-install-recommends git && rm -rf /var/lib/apt/lists/*
RUN pip install --no-cache-dir \\
    {pip_install_lines}
COPY . .
ENV PYTHONUNBUFFERED=1
ENV RUFO_POLICY_PATH={policy_file}
ENV RUFO_AGENT_MODULE={entrypoint_module}
ENV RUFO_AGENT_ENTRYPOINT={manifest.entrypoint_attr}
{deploy_config_env}ENV RUFO_DB_PATH=/app/rufo_data/rufo.db
EXPOSE 8080
CMD ["sh", "-c", "uvicorn rufo_runtime.app:app --host 0.0.0.0 --port ${{PORT:-8080}}"]
"""
