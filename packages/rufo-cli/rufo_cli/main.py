from __future__ import annotations

import os
from pathlib import Path

import requests
import typer
from rich.console import Console
from rich.table import Table
from rufo_core.manifest import load_manifest

from rufo_cli.scaffold import scaffold

app = typer.Typer(help="Rufo: deploy and operate policy-governed AI agents.")
approvals_app = typer.Typer(help="Inspect and decide pending approval requests.")
app.add_typer(approvals_app, name="approvals")

console = Console()


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
) -> None:
    """Deploy an agent behind the Rufo runtime: policy enforcement, approval
    queue, audit log, and an OpenAI-compatible /v1/chat/completions endpoint,
    served over HTTP. Reads rufo.toml/rufo.yaml if the target is a directory
    (or contains one); otherwise falls back to a plain .py file + --policy.
    This runs the runtime in the foreground -- Ctrl-C to stop.
    """
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
