from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from langsmith import Client
from rich.console import Console

from src.config import settings
from src.utils import logger

console = Console()


class LangSmithAnalyzer:
    """Service to analyze execution flows using LangSmith data."""

    def __init__(self, project_name: str | None = None) -> None:
        self.client = Client()
        self.project_name = project_name or settings.tracing.project_name

    async def generate_report(self, trace_id: str) -> str:
        """Fetches trace data and generates a detailed flow analysis report."""
        try:
            sorted_runs = await self._fetch_sorted_runs(trace_id)
            if not sorted_runs:
                return f"No runs found for trace ID: {trace_id}"

            return self._format_report(trace_id, sorted_runs)

        except Exception as e:
            logger.exception(f"Failed to generate LangSmith report for {trace_id}")
            return f"Error generating report: {e}"

    async def _fetch_sorted_runs(self, trace_id: str) -> list[Any]:
        """Fetches and sorts runs by start time."""
        runs = list(self.client.list_runs(trace_id=trace_id, project_name=self.project_name))
        return sorted(runs, key=lambda x: x.start_time)

    def _format_report(self, trace_id: str, sorted_runs: list[Any]) -> str:
        """Formats the sorted runs into a markdown report."""
        report = [
            "# LangSmith Flow Analysis Report",
            f"**Trace ID:** `{trace_id}`",
            f"**Project:** `{self.project_name}`",
            f"**Generated at:** {datetime.now(UTC).isoformat()}",
            "\n## Execution Timeline\n",
            "| Start Time | Duration | Node Name | Status | Notes |",
            "| :--- | :--- | :--- | :--- | :--- |",
        ]

        node_counts: dict[str, int] = {}
        for run in sorted_runs:
            report.append(self._format_run_row(run))
            node_counts[run.name] = node_counts.get(run.name, 0) + 1

        report.append("\n## Logical Insights")
        for node, count in node_counts.items():
            if count > 5:
                report.append(f"- 🔄 **Node Loop:** `{node}` executed {count} times.")

        return "\n".join(report)

    def _format_run_row(self, run: Any) -> str:
        """Formats a single run into a table row."""
        start_str = run.start_time.strftime("%H:%M:%S.%f")[:-3]
        duration = "N/A"
        notes = []

        if run.end_time:
            d = (run.end_time - run.start_time).total_seconds()
            duration = f"{d * 1000:.0f}ms" if d < 1 else f"{d:.2f}s"
            if d * 1000 < 50 and run.name in ["monitor", "monitor_session"]:
                notes.append("⚠️ **Ultra-fast loop** (potential stale result)")

        if run.status == "error":
            notes.append("❌ **Error detected**")

        return f"| {start_str} | {duration} | {run.name} | {run.status or 'pending'} | {' '.join(notes)} |"

    async def fetch_node_details(self, trace_id: str, node_name: str) -> list[dict[str, Any]]:
        """Fetches detailed inputs and outputs for specific nodes in a trace."""
        runs = list(self.client.list_runs(trace_id=trace_id, project_name=self.project_name))
        matches = [
            r
            for r in runs
            if r.name == node_name
            or (r.extra or {}).get("metadata", {}).get("langgraph_node") == node_name
        ]

        results = []
        for r in matches:
            results.append(
                {
                    "id": str(r.id),
                    "name": r.name,
                    "start_time": r.start_time.isoformat(),
                    "inputs": r.inputs,
                    "outputs": r.outputs,
                    "metadata": r.extra.get("metadata") if r.extra else None,
                }
            )
        return results

    async def analyze_staleness(self, trace_id: str) -> str:
        """Deep analysis of Jules session timing to detect stale results."""
        runs = list(self.client.list_runs(trace_id=trace_id, project_name=self.project_name))

        # Look for JulesClient or monitor nodes
        monitor_runs = sorted(
            [r for r in runs if r.name in ["monitor", "monitor_session"]],
            key=lambda x: x.start_time,
        )

        if not monitor_runs:
            return "No monitoring nodes found in trace."

        analysis = ["## Staleness Analysis"]
        for i, run in enumerate(monitor_runs):
            if run.end_time:
                duration_ms = (run.end_time - run.start_time).total_seconds() * 1000
                if duration_ms < 100:
                    analysis.append(
                        f"- 🚩 **Potential Stale Result:** Node `{run.name}` (Step {i + 1}) finished in {duration_ms:.0f}ms."
                    )
                    analysis.append(
                        f"  - Input Jules State: `{(run.inputs or {}).get('jules_state') or 'Unknown'}`"
                    )
                    analysis.append(
                        f"  - Output Status: `{(run.outputs or {}).get('status') or 'Unknown'}`"
                    )

        if len(analysis) == 1:
            analysis.append("- No obvious staleness detected based on timing.")

        return "\n".join(analysis)

    def save_report(self, cycle_id: str, report_text: str) -> Path:
        """Saves the diagnostic report to the logs directory."""
        timestamp = datetime.now(UTC).strftime("%Y%m%d_%H%M%S")
        diag_dir = Path("logs/diagnostics")
        diag_dir.mkdir(parents=True, exist_ok=True)

        report_path = diag_dir / f"trace_cycle_{cycle_id}_{timestamp}.md"
        report_path.write_text(report_text, encoding="utf-8")
        return report_path
