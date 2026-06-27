import os
from typing import Any

import litellm

from src.domain_models import LangSmithConfig, TracingMetadata


class TracingService:
    """Service to manage LangSmith tracing."""

    def __init__(self, config: LangSmithConfig) -> None:
        self.config = config
        if self.is_enabled:
            self.register_litellm_tracing()

    def register_litellm_tracing(self) -> None:
        """Enables LiteLLM tracing for LangSmith if the environment is configured."""
        if "langsmith" not in litellm.success_callback:
            litellm.success_callback.append("langsmith")
        if "langsmith" not in litellm.failure_callback:
            litellm.failure_callback.append("langsmith")

        # Ensure project name is explicitly set for LiteLLM
        project_name = self.config.project_name or os.environ.get("LANGSMITH_PROJECT")
        if project_name:
            os.environ["LANGSMITH_PROJECT"] = project_name
            os.environ["LANGCHAIN_PROJECT"] = project_name

    @property
    def is_enabled(self) -> bool:
        # Check environment variable if config override is not set or false
        env_enabled = os.environ.get("LANGCHAIN_TRACING_V2", "false").lower() == "true"
        return self.config.tracing_enabled or env_enabled

    def get_run_config(self, metadata: TracingMetadata) -> dict[str, Any]:
        """Get RunnableConfig compatible kwargs for LangGraph."""
        return metadata.to_langchain_kwargs()
