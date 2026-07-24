from __future__ import annotations

import io
import os
import tarfile
import time
import webbrowser
from pathlib import Path

import requests
import typer
from rich.console import Console
from rich.table import Table
from rufo_core.manifest import load_manifest

from rufo_cli.credentials import load_token, save_token
from rufo_cli.scaffold import scaffold

RUFO_CLOUD_URL = "https://rufo.eldridgemorgan.com"

app = typer.Typer(help="Rufo: deploy and operate policy-governed AI agents.")
approvals_app = typer.Typer(help="Inspect and decide pending approval requests.")
app.add_typer(approvals_app, name="approvals")

console = Console()


@app.command()
def login(
    url: str = typer.Option(RUFO_CLOUD_URL, "--url", help="Rufo Cloud control-plane URL"),
) -> None:
    """Authenticate the CLI with Rufo Cloud via a browser-based device flow --
    approve the printed code at <url>/device, the same pattern as `gh auth login`.
    """
    resp = requests.post(f"{url}/api/auth/device/start", timeout=10)
    resp.raise_for_status()
    body = resp.json()
    device_code, user_code, expires_in = body["device_code"], body["user_code"], body["expires_in"]

    verification_url = f"{url}/device"
    console.print(f"\nGo to [bold]{verification_url}[/bold] and enter code: [bold cyan]{user_code}[/bold cyan]\n")
    try:
        webbrowser.open(verification_url)
    except Exception:  # noqa: BLE001 - opening a browser is best-effort
        pass

    deadline = time.time() + expires_in
    with console.status("Waiting for approval..."):
        while time.time() < deadline:
            poll = requests.get(f"{url}/api/auth/device/poll", params={"device_code": device_code}, timeout=10)
            poll.raise_for_status()
            poll_body = poll.json()
            if poll_body["status"] == "approved":
                save_token(url, poll_body["token"])
                console.print("[green]Logged in.[/green]")
                return
            if poll_body["status"] in ("expired", "not_found"):
                console.print("[red]Login code expired or invalid -- run `rufo login` again.[/red]")
                raise typer.Exit(1)
            time.sleep(2)

    console.print("[red]Timed out waiting for approval.[/red]")
    raise typer.Exit(1)


def _require_cloud_credentials(url_override: str | None) -> tuple[str, str]:
    creds = load_token()
    if creds is None:
        console.print("[red]Not logged in.[/red] Run [bold]rufo login[/bold] first.")
        raise typer.Exit(1)
    return url_override or creds["url"], creds["token"]


# Never worth shipping to a build context: VCS metadata, caches, local venvs,
# and the runtime's own local-mode data directory.
_TAR_EXCLUDE_DIR_NAMES = {".git", "__pycache__", ".venv", "venv", "node_modules", "rufo_data", ".mypy_cache", ".pytest_cache"}


def _tar_agent_dir(agent_dir: Path) -> bytes:
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        for path in sorted(agent_dir.rglob("*")):
            if path.is_dir():
                continue
            rel = path.relative_to(agent_dir)
            if _TAR_EXCLUDE_DIR_NAMES & set(rel.parts[:-1]):
                continue
            tar.add(path, arcname=str(rel))
    return buf.getvalue()


def _deploy_cloud(target: Path, url_override: str | None) -> None:
    agent_dir = target if target.is_dir() else target.parent
    manifest_path = agent_dir / "rufo.toml"
    if not manifest_path.exists():
        console.print(f"[red]no rufo.toml found in[/red] {agent_dir}")
        raise typer.Exit(1)

    base_url, token = _require_cloud_credentials(url_override)
    tar_bytes = _tar_agent_dir(agent_dir)
    console.print(f"[dim]uploading {agent_dir} ({len(tar_bytes)} bytes) to {base_url}...[/dim]")

    with console.status("Building and deploying (this can take a few minutes)..."):
        try:
            resp = requests.post(
                f"{base_url}/api/deployments/from-source",
                headers={"Authorization": f"Bearer {token}"},
                files={"source": ("agent.tar.gz", tar_bytes, "application/gzip")},
                timeout=900,
            )
        except requests.RequestException as exc:
            console.print(f"[red]could not reach {base_url}:[/red] {exc}")
            raise typer.Exit(1) from exc

    if not resp.ok:
        try:
            detail = resp.json().get("detail", resp.text)
        except ValueError:
            detail = resp.text
        console.print(f"[red]deploy failed ({resp.status_code}):[/red] {detail}")
        raise typer.Exit(1)

    body = resp.json()
    console.print(f"[green]Deployed.[/green] {body.get('agent_name')} -> [bold]{body.get('uri')}[/bold]")


@app.command()
def init(dir: Path = typer.Argument(Path("."), help="Directory to scaffold into.")) -> None:
    """Scaffold a starter policy.yaml, rufo.toml, rufo.yaml, and example LangGraph agent."""
    written = scaffold(dir)
    if not written:
        console.print("[yellow]Nothing written -- files already exist.[/yellow]")
        return
    for path in written:
        console.print(f"[green]created[/green] {path}")
    console.print(f"\nNext: [bold]rufo deploy {dir}[/bold]")


@app.command()
def deploy(
    target: Path = typer.Argument(
        Path("."), help="Path to an agent .py file, or a directory containing rufo.toml"
    ),
    policy: Path = typer.Option(None, "--policy", "-p", help="Override the policy.yaml path"),
    entrypoint: str = typer.Option(None, "--entrypoint", "-e", help="Override the entrypoint attribute name"),
    port: int = typer.Option(8000, "--port", help="Port to serve the runtime on"),
    db: Path = typer.Option(Path("./rufo_data/rufo.db"), "--db", help="SQLite path for approvals/audit"),
    cloud: bool = typer.Option(
        False, "--cloud", help="Deploy to Rufo Cloud instead of running locally (requires `rufo login`)"
    ),
    url: str = typer.Option(None, "--url", help="Rufo Cloud control-plane URL (defaults to the one used at login)"),
) -> None:
    """Deploy an agent behind the Rufo runtime: policy enforcement, approval
    queue, audit log, and an OpenAI-compatible /v1/chat/completions endpoint.
    Reads rufo.toml/rufo.yaml if the target is a directory (or contains one);
    otherwise falls back to a plain .py file + --policy.

    By default this runs the runtime locally in the foreground (Ctrl-C to
    stop). Pass --cloud to instead upload the agent directory to Rufo Cloud
    and have it built and deployed to Cloud Run.
    """
    if cloud:
        _deploy_cloud(target, url)
        return

    agent_dir = target if target.is_dir() else target.parent
    manifest_path = agent_dir / "rufo.toml"
    deploy_config_path = agent_dir / "rufo.yaml"

    if target.is_dir() or manifest_path.exists():
        if not manifest_path.exists():
            console.print(f"[red]no rufo.toml found in[/red] {agent_dir}")
            raise typer.Exit(1)
        manifest = load_manifest(manifest_path)
        agent_module = agent_dir / manifest.entrypoint_module
        entry_attr = entrypoint or manifest.entrypoint_attr
        policy_path = policy or (agent_dir / manifest.policy_file)
        console.print(f"[dim]using {manifest_path}[/dim] (entrypoint={manifest.entrypoint_module}:{entry_attr})")
    else:
        agent_module = target
        entry_attr = entrypoint or "agent"
        policy_path = policy or Path("policy.yaml")

    if not agent_module.exists():
        console.print(f"[red]agent module not found:[/red] {agent_module}")
        raise typer.Exit(1)
    if not policy_path.exists():
        console.print(f"[red]policy file not found:[/red] {policy_path}")
        raise typer.Exit(1)

    os.environ["RUFO_POLICY_PATH"] = str(policy_path.resolve())
    os.environ["RUFO_AGENT_MODULE"] = str(agent_module.resolve())
    os.environ["RUFO_AGENT_ENTRYPOINT"] = entry_attr
    os.environ["RUFO_DB_PATH"] = str(db)
    if deploy_config_path.exists():
        os.environ["RUFO_DEPLOY_CONFIG_PATH"] = str(deploy_config_path.resolve())
        console.print(f"[dim]using {deploy_config_path}[/dim]")

    console.print(f"[bold green]rufo[/bold green] deploying {agent_module} with policy {policy_path}")
    console.print(f"  -> http://localhost:{port}")
    console.print("     workflow: /invoke /resume /approvals /audit")
    console.print("     endpoint: /v1/chat/completions /v1/models")

    import uvicorn

    uvicorn.run("rufo_runtime.app:app", host="0.0.0.0", port=port, log_level="info")


@approvals_app.command("list")
def approvals_list(
    status: str = typer.Option(None, "--status", help="Filter: pending | approved | rejected"),
    url: str = typer.Option("http://localhost:8000", "--url", help="Runtime base URL"),
) -> None:
    """List approval requests."""
    resp = requests.get(f"{url}/approvals", params={"status": status} if status else {})
    resp.raise_for_status()
    rows = resp.json()

    table = Table(title="Rufo Approvals")
    for col in ("id", "run_id", "tool_name", "reason", "status", "created_at"):
        table.add_column(col)
    for row in rows:
        table.add_row(*(str(row.get(c, "")) for c in ("id", "run_id", "tool_name", "reason", "status", "created_at")))
    console.print(table)


@approvals_app.command("decide")
def approvals_decide(
    approval_id: str = typer.Argument(...),
    approve: bool = typer.Option(..., "--approve/--reject", help="Approve or reject the request"),
    reason: str = typer.Option(None, "--reason", help="Reason recorded in the audit log"),
    url: str = typer.Option("http://localhost:8000", "--url", help="Runtime base URL"),
) -> None:
    """Approve or reject a pending approval request."""
    resp = requests.post(f"{url}/approvals/{approval_id}/decide", json={"approved": approve, "reason": reason})
    resp.raise_for_status()
    console.print(resp.json())


@app.command()
def logs(
    run_id: str = typer.Option(None, "--run-id", help="Filter to a single run"),
    limit: int = typer.Option(50, "--limit"),
    url: str = typer.Option("http://localhost:8000", "--url", help="Runtime base URL"),
) -> None:
    """Show the audit trail."""
    params = {"limit": limit}
    if run_id:
        params["run_id"] = run_id
    resp = requests.get(f"{url}/audit", params=params)
    resp.raise_for_status()
    rows = resp.json()

    table = Table(title="Rufo Audit Log")
    for col in ("created_at", "run_id", "event_type", "tool_name", "payload"):
        table.add_column(col)
    for row in rows:
        table.add_row(*(str(row.get(c, "")) for c in ("created_at", "run_id", "event_type", "tool_name", "payload")))
    console.print(table)


if __name__ == "__main__":
    app()
