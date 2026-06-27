"""Configuration domain models."""

from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator


class DispatcherConfig(BaseModel):
    """Configuration for the async dispatcher."""

    model_config = ConfigDict(extra="forbid")

    max_concurrent_tasks: int = Field(
        default=10, ge=1, description="Maximum number of concurrent tasks"
    )
    retry_backoff_factor: float = Field(
        default=2.0, gt=0, description="Backoff factor for retry on 429"
    )
    max_retries: int = Field(
        default=5, ge=0, description="Maximum number of retries for API requests"
    )


class ObservabilityConfig(BaseModel):
    """Authoritative contract for required observability environment variables."""

    model_config = ConfigDict(extra="forbid")

    langchain_tracing_v2: str | bool = Field(
        ..., description="Must be 'true' or True to enable LangSmith tracing."
    )
    langchain_api_key: str = Field(..., min_length=1, description="LangSmith API Key.")
    langchain_project: str = Field(..., min_length=1, description="LangSmith Project Name.")

    @field_validator("langchain_tracing_v2")
    @classmethod
    def validate_tracing_enabled(cls, v: Any) -> bool | str:
        if isinstance(v, str):
            if v.lower() != "true":
                msg = "LANGCHAIN_TRACING_V2 must be 'true'"
                raise ValueError(msg)
            return "true"
        if isinstance(v, bool):
            if not v:
                msg = "LANGCHAIN_TRACING_V2 must be True"
                raise ValueError(msg)
            return True
        msg = "LANGCHAIN_TRACING_V2 must be a boolean or 'true'"
        raise ValueError(msg)

    @field_validator("langchain_api_key", "langchain_project")
    @classmethod
    def validate_non_empty(cls, v: str) -> str:
        if not v or not v.strip():
            msg = "Value cannot be empty or whitespace."
            raise ValueError(msg)
        return v


class LangSmithConfig(BaseModel):
    """Configuration for LangSmith tracing."""

    tracing_enabled: bool = Field(default=False, alias="LANGCHAIN_TRACING_V2")
    api_key: str | None = Field(default=None, alias="LANGCHAIN_API_KEY")
    project_name: str = Field(default="nitpickers-default", alias="LANGCHAIN_PROJECT")
    endpoint: str = Field(default="https://api.smith.langchain.com", alias="LANGCHAIN_ENDPOINT")


class TracingMetadata(BaseModel):
    """Standardized metadata payload attached to every LangSmith trace."""

    session_id: str = Field(description="Unique identifier for the current session")
    execution_type: str = Field(description="e.g., 'jules_session', 'batch_audit', 'cli_run'")
    git_branch: str | None = Field(default=None)
    custom_metadata: dict[str, Any] = Field(default_factory=dict)

    def to_langchain_kwargs(self) -> dict[str, Any]:
        tags = [self.execution_type]
        if self.git_branch:
            tags.append(f"branch:{self.git_branch}")
        return {
            "run_name": f"Workflow_{self.execution_type.capitalize()}",
            "tags": tags,
            "metadata": {
                "session_id": self.session_id,
                **self.custom_metadata,
            },
        }
