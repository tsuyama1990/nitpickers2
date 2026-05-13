from datetime import UTC, datetime
from pathlib import Path

from src.services.diagnostics import LangSmithAnalyzer
from src.services.rca_service import RCAService
from src.utils import logger


class PostMortemService:
    """
    Consolidated diagnostic service that combines LangSmith flow analysis
    with AI-driven Root Cause Analysis (RCA).
    """

    def __init__(self, project_name: str | None = None, rca_model: str | None = None) -> None:
        self.analyzer = LangSmithAnalyzer(project_name)
        self.rca = RCAService(rca_model)

    async def generate_full_report(
        self, cycle_id: str, trace_id: str, snapshot_path: Path | None = None
    ) -> Path:
        """
        Generates a comprehensive diagnostic report including flow analysis
        and (if snapshot is provided) Root Cause Analysis.
        """
        logger.info(f"Generating full post-mortem for cycle {cycle_id} (Trace: {trace_id})")

        # 1. Generate LangSmith Flow Report
        flow_report = await self.analyzer.generate_report(trace_id)
        staleness_analysis = await self.analyzer.analyze_staleness(trace_id)

        # 2. Generate RCA (if possible)
        rca_report = ""
        import asyncio

        if snapshot_path:
            exists = await asyncio.to_thread(snapshot_path.exists)
            if exists:
                rca_report = await self.rca.analyze_failure(cycle_id, snapshot_path)

        # 3. Consolidate
        full_report = [
            f"# Post-Mortem Report: Cycle {cycle_id}",
            f"**Generated:** {datetime.now(UTC).isoformat()}",
            f"**Trace ID:** `{trace_id}`",
            "\n---\n",
            rca_report
            if rca_report
            else "## Root Cause Analysis\n*No state snapshot provided for AI analysis.*",
            "\n---\n",
            flow_report,
            "\n---\n",
            staleness_analysis,
        ]

        report_text = "\n".join(full_report)

        # 4. Save to logs
        report_path = self.analyzer.save_report(cycle_id, report_text)
        logger.info(f"Post-mortem report saved to: {report_path}")

        return report_path
