"""CLI module."""

import asyncio
import os
from pathlib import Path

import typer
from rich.console import Console

from src.config import settings
from src.services.project import ProjectManager
from src.services.workflow import WorkflowService

app = typer.Typer()
console = Console()


def _resolve_templates_path() -> str:
    """Resolve the system prompt templates directory.

    Resolution priority:
    1. ``NITPICK_TEMPLATE_PATH`` environment variable (set by Docker ``Dockerfile``)
    2. ``importlib.resources`` — works for both ``uvx`` (installed package) and ``uv run`` (local dev)
    3. Filesystem fallback relative to this file (legacy local dev)
    """
    env_path = os.environ.get("NITPICK_TEMPLATE_PATH")
    if env_path:
        return env_path

    try:
        from importlib import resources

        return str(resources.files("src").joinpath("templates"))
    except (ImportError, TypeError, ModuleNotFoundError):
        return str(Path(__file__).parent / "templates")


@app.command()
def init() -> None:
    """Initialize a new target project for Nitpick."""
    console.print("[bold blue]Initializing new Nitpick project...[/bold blue]")
    manager = ProjectManager()
    templates_path = _resolve_templates_path()

    try:
        asyncio.run(manager.initialize_project(templates_path))

        console.print("[bold green]Initialization complete![/bold green]")
        console.print("\n[bold]Next Steps:[/bold]")
        console.print(
            "1. Update [cyan]dev_documents/ALL_SPEC.md[/cyan] with your project specifications."
        )
        console.print(
            "2. Update [cyan]dev_documents/USER_TEST_SCENARIO.md[/cyan] with your tutorial/UAT plan."
        )
        console.print(
            "3. Ensure your required environment variables are listed in [cyan]dev_documents/required_envs.json[/cyan]."
        )
        console.print("4. Add required environment variables to the root [cyan].env[/cyan] file.")
        console.print(
            "5. Run [bold magenta]nitpick gen-cycles[/bold magenta] to architect your development plan."
        )

    except Exception as e:
        console.print(f"[bold red]Initialization failed:[/bold red] {e}")


@app.command()
def gen_cycles(
    cycles: int = typer.Option(
        settings.default_cycles_count, "--cycles", "-c", help="Number of cycles to generate"
    ),
    session: str | None = typer.Option(None, "--session", help="Session ID"),
) -> None:
    """Generate architecture and development cycles."""
    service = WorkflowService()
    asyncio.run(service.run_gen_cycles(cycles, project_session_id=session))


@app.command()
def run_cycle(
    cycle_id: str = typer.Option("all", "--id", "-i", help="Cycle ID to run (e.g., '01') or 'all'"),
    resume: bool = typer.Option(False, "--resume", "-r", help="Resume from last failed node"),
    auto: bool = typer.Option(False, "--auto", "-a", help="Auto-approve AI decisions"),
    start_iter: int = typer.Option(1, "--start-iter", "-s", help="Starting iteration count"),
    session: str = typer.Option(None, "--session", help="Session ID (if not using current state)"),
    parallel: bool = typer.Option(
        True, "--parallel/--no-parallel", "-p", help="Run multiple cycles concurrently based on DAG"
    ),
) -> None:
    """Run one or all development cycles."""
    service = WorkflowService()

    # Pre-flight environment check
    service.verify_environment_and_observability()

    asyncio.run(
        service.run_cycle(
            cycle_id=cycle_id,
            resume=resume,
            auto=auto,
            start_iter=start_iter,
            project_session_id=session,
            parallel=parallel,
        )
    )


@app.command()
def run_pipeline(
    session: str | None = typer.Option(
        None, "--session", help="Session ID (if not using current state)"
    ),
    parallel: bool = typer.Option(
        True, "--parallel/--no-parallel", help="Enable parallel execution"
    ),
) -> None:
    """Run the complete orchestrated 5-Phase pipeline."""
    service = WorkflowService()
    asyncio.run(service.run_full_pipeline(project_session_id=session, parallel=parallel))


@app.command()
def integrate(session: str | None = typer.Option(None, "--session", help="Session ID")) -> None:
    """Phase 3: Run the Integration Graph."""
    service = WorkflowService()
    asyncio.run(service.run_integration_phase(project_session_id=session))


@app.command()
def uat(session: str | None = typer.Option(None, "--session", help="Session ID")) -> None:
    """Phase 4: Run the QA/UAT Graph."""
    service = WorkflowService()
    asyncio.run(service.run_qa_phase(project_session_id=session))


@app.command()
def finalize_session(
    session: str | None = typer.Option(None, "--session", help="Session ID"),
) -> None:
    """Finalize the current working session."""
    service = WorkflowService()
    asyncio.run(service.finalize_session(project_session_id=session))


@app.command()
def check_api(
    model: str = typer.Option(None, "--model", "-m", help="Model to use for connectivity check"),
    message: str = typer.Option("Hello, are you there?", "--message", "-p", help="Message to send"),
) -> None:
    """Verify OpenRouter API connectivity."""
    import litellm

    console.print("[bold cyan]Checking OpenRouter Connectivity...[/bold cyan]")

    if not settings.OPENROUTER_API_KEY or not settings.OPENROUTER_API_KEY.get_secret_value():
        console.print("[bold red]Error:[/bold red] OPENROUTER_API_KEY is not set.")
        raise typer.Exit(code=1)

    target_model = model or settings.agents.auditor_model
    console.print(f"Model: [green]{target_model}[/green]")

    try:
        response = asyncio.run(
            litellm.acompletion(
                model=target_model,
                messages=[{"role": "user", "content": message}],
                max_tokens=100,
            )
        )
        content = response.choices[0].message.content
        console.print("[bold green]Success![/bold green] API is reachable.")
        if content:
            console.print("\n[bold]Response:[/bold]")
            console.print(f"[dim]{content.strip()}[/dim]")
    except Exception as e:
        console.print(f"[bold red]Failed to connect to OpenRouter:[/bold red] {e}")
        if not model and "404" in str(e):
            console.print(
                "[yellow]Tip:[/yellow] The default model might be unavailable. Try specifying a model with [cyan]--model[/cyan]."
            )
        raise typer.Exit(code=1) from e
