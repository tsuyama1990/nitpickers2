import contextvars
import json as _json
import logging
import os
import re as _re
import shutil
import subprocess
import unicodedata as _unicodedata
from types import TracebackType
from typing import Any

from dotenv import load_dotenv
from langchain_core.callbacks import BaseCallbackHandler
from rich.console import Console
from rich.logging import RichHandler

console = Console()

current_cycle_id: contextvars.ContextVar[str] = contextvars.ContextVar("cycle_id", default="CORE")
current_trace_id: contextvars.ContextVar[str] = contextvars.ContextVar("trace_id", default="N/A")


class CycleFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        record.cycle_id = current_cycle_id.get()
        record.trace_id = current_trace_id.get()
        return True


class TraceIdCallbackHandler(BaseCallbackHandler):
    """Callback handler to sync LangGraph run_id with local contextvars."""

    def on_chain_start(
        self, serialized: dict[str, Any], inputs: dict[str, Any], **kwargs: Any
    ) -> Any:
        run_id = kwargs.get("run_id")
        if run_id:
            current_trace_id.set(str(run_id))

    def on_node_start(
        self, serialized: dict[str, Any], inputs: dict[str, Any], **kwargs: Any
    ) -> Any:
        """Called at the start of each LangGraph node."""
        # Restore context from config to handle potential thread-pool context loss
        config = kwargs.get("config")
        if config:
            sync_context_from_config(config)


def sync_context_from_config(config: Any) -> None:
    """Restores cycle_id and trace_id from LangGraph RunnableConfig."""
    if not config:
        return

    # Extract from configurable
    if hasattr(config, "get"):
        configurable = config.get("configurable", {})
    elif hasattr(config, "configurable"):
        configurable = getattr(config, "configurable", {})
    else:
        configurable = {}

    cid = configurable.get("cycle_id")
    if cid:
        current_cycle_id.set(str(cid))

    # Extract trace_id (run_id) if available
    tid = getattr(config, "run_id", None)
    if tid:
        current_trace_id.set(str(tid))


class ResilientRichHandler(RichHandler):
    """RichHandler that gracefully handles missing 'cycle_id' and 'trace_id'."""

    def emit(self, record: logging.LogRecord) -> None:
        if not hasattr(record, "cycle_id"):
            record.cycle_id = current_cycle_id.get()
        if not hasattr(record, "trace_id"):
            record.trace_id = current_trace_id.get()
        super().emit(record)


# Logger configuration
logging.basicConfig(
    level="INFO",
    format="[%(cycle_id)s] [%(trace_id)s] %(message)s",
    datefmt="[%X]",
    handlers=[ResilientRichHandler(console=console, rich_tracebacks=True)],
)

logger = logging.getLogger("AC-CDD")
logger.addFilter(CycleFilter())


def setup_cycle_logging(cycle_id: str) -> None:
    """Attaches a file handler for specific cycle logging."""
    from pathlib import Path

    log_file = Path(f"logs/cycles/cycle_{cycle_id}.log")
    log_file.parent.mkdir(parents=True, exist_ok=True)

    file_handler = logging.FileHandler(log_file)
    file_handler.setFormatter(
        logging.Formatter("[%(asctime)s] [%(levelname)s] [%(trace_id)s] %(message)s")
    )
    logger.addHandler(file_handler)
    logger.info(f"Initialized isolated logging for Cycle {cycle_id} -> {log_file}")


def run_command(
    command: list[str], cwd: str | None = None, env: dict[str, str] | None = None
) -> None:
    """
    Execute a command and display output in real-time.
    Raises CalledProcessError on error.
    """
    cmd_str = " ".join(command)
    logger.info(f"Running: {redact_secrets(cmd_str)}")

    try:
        process = subprocess.Popen(
            command,
            cwd=cwd,
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
        )

        if process.stdout:
            for _line in process.stdout:
                pass  # Direct output instead of via RichHandler to show raw logs

        process.wait()

    except Exception:
        logger.exception("Command failed")
        raise

    if process.returncode != 0:
        raise subprocess.CalledProcessError(process.returncode, command)


def check_api_key() -> bool:
    """
    Checks if the necessary API keys are set in the environment.
    Returns True if keys are found, False otherwise.
    """

    load_dotenv()

    # Check for common API keys
    google_key = os.getenv("GOOGLE_API_KEY")
    openrouter_key = os.getenv("OPENROUTER_API_KEY")

    if not google_key and not openrouter_key:
        logger.warning(
            "API Key not found! (GOOGLE_API_KEY or OPENROUTER_API_KEY). "
            "Proceeding assuming this is a test or dry-run. "
            "Real operations will fail."
        )
        return False
    return True


def get_command_prefix() -> str:
    """Get the appropriate command prefix based on environment."""
    import os
    from pathlib import Path

    # Method 1: Check for .dockerenv file
    if Path("/.dockerenv").exists():
        return "docker-compose run --rm ac-cdd ac-cdd"

    # Method 2: Check cgroup for docker
    try:
        with Path("/proc/self/cgroup").open() as f:
            if "docker" in f.read():
                return "docker-compose run --rm ac-cdd ac-cdd"
    except (FileNotFoundError, PermissionError):
        pass

    # Method 3: Check environment variable
    if os.environ.get("DOCKER_CONTAINER") == "true":
        return "docker-compose run --rm ac-cdd ac-cdd"

    return "uv run manage.py"


class KeepAwake:
    """
    Context manager to prevent system sleep/suspension during long operations.
    Uses 'systemd-inhibit' on Linux.
    """

    def __init__(self, reason: str = "AC-CDD Long Running Task") -> None:
        self.reason = reason
        self.process: subprocess.Popen[bytes] | None = None

    def __enter__(self) -> "KeepAwake":
        """Start the inhibitor process."""
        # Check if systemd-inhibit exists
        if not shutil.which("systemd-inhibit"):
            if os.environ.get("DOCKER_CONTAINER") == "true":
                logger.debug(
                    "systemd-inhibit not found (running in Docker). Sleep inhibition disabled."
                )
            else:
                logger.warning("systemd-inhibit not found. Sleep inhibition disabled.")
            return self

        try:
            # We start a subprocess that holds the lock forever (sleep infinity)
            # When this python process exits or we kill the subprocess, the lock is released.
            # --what=idle:sleep:handle-suspend-key:handle-hibernate-key:handle-lid-switch
            # We strictly want to prevent sleep/suspend.
            cmd = [
                "systemd-inhibit",
                "--what=idle:sleep",
                "--who=AC-CDD",
                f"--why={self.reason}",
                "--mode=block",
                "sleep",
                "infinity",
            ]
            self.process = subprocess.Popen(
                cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
            )
            logger.info("💤 System sleep inhibited (AC-CDD is running).")
        except Exception:
            logger.exception("Failed to start sleep inhibitor")
        return self

    def __exit__(
        self,
        _exc_type: type[BaseException] | None,
        _exc_val: BaseException | None,
        _exc_tb: TracebackType | None,
    ) -> None:
        """Stop the inhibitor process."""
        if self.process:
            try:
                self.process.terminate()
                self.process.wait(timeout=1)
            except Exception:
                # If it refuses to die, kill it
                logger.debug("Force killing sleep inhibitor")
                if self.process.poll() is None:
                    self.process.kill()
            logger.info("💤 System sleep inhibition released.")


# ---------------------------------------------------------------------------
#  JSON extraction  (consolidated from utils_json.py)
# ---------------------------------------------------------------------------


_THOUGHT_BLOCK_RE = _re.compile(r"<thought>.*?</thought>", flags=_re.DOTALL | _re.IGNORECASE)
_TRUNCATED_THOUGHT_BLOCK_RE = _re.compile(r"<thought>.*", flags=_re.DOTALL | _re.IGNORECASE)
_MARKDOWN_JSON_BLOCK_RE = _re.compile(
    r"```(?:json|python)?\s*(.*?)\s*```", flags=_re.DOTALL | _re.IGNORECASE
)


def _repair_json(json_str: str) -> str:
    """Simple JSON repair for truncated EOF strings"""
    stack: list[str] = []
    in_string = False
    escaped = False
    repaired = ""
    for char in json_str:
        if char == '"' and not escaped:
            in_string = not in_string
        if not in_string:
            if char in {"{", "["}:
                stack.append(char)
            elif (char == "}" and stack and stack[-1] == "{") or (
                char == "]" and stack and stack[-1] == "["
            ):
                stack.pop()
        repaired += char
        escaped = char == "\\" and not escaped
    if in_string:
        repaired += '"'
    while stack:
        last = stack.pop()
        repaired += "}" if last == "{" else "]"
    return repaired


def extract_json_from_text(content: str) -> str:
    """Extracts JSON from an LLM response, stripping markdown and <thought> tags."""
    content = _THOUGHT_BLOCK_RE.sub("", content)
    content = _TRUNCATED_THOUGHT_BLOCK_RE.sub("", content)

    blocks = _MARKDOWN_JSON_BLOCK_RE.findall(content)
    for block in blocks:
        repaired = _repair_json(block.strip())
        try:
            parsed = _json.loads(repaired)
            if isinstance(parsed, (dict, list)):
                return repaired
        except _json.JSONDecodeError:
            continue

    start_idx = content.find("{")
    if start_idx != -1:
        json_str = content[start_idx:].strip()
        repaired = _repair_json(json_str)
        try:
            parsed = _json.loads(repaired)
            if isinstance(parsed, (dict, list)):
                return repaired
        except _json.JSONDecodeError:
            pass
        return repaired

    return _repair_json(content.strip())


# ---------------------------------------------------------------------------
#  Sanitization  (consolidated from utils_sanitization.py)
# ---------------------------------------------------------------------------



def redact_secrets(content: str) -> str:
    """Redacts common API keys and sensitive patterns from the text."""
    patterns = [
        (_re.compile(r"(sk-[a-zA-Z0-9-]{24,})"), "[REDACTED_API_KEY]"),
        (_re.compile(r"(AIza[0-9A-Za-z-_]{35})"), "[REDACTED_GOOGLE_KEY]"),
        (_re.compile(r"(pass(?:word)?[:=]\s*)(\S+)"), r"\1[REDACTED_PASSWORD]"),
    ]
    redacted = content
    for pattern, replacement in patterns:
        redacted = pattern.sub(replacement, redacted)
    return redacted


def sanitize_for_llm(content: str, max_length: int = 100000) -> str:
    """Sanitizes arbitrary text before inclusion in LLM prompts."""
    if not content:
        return ""
    redacted = redact_secrets(content)
    truncated = redacted[:max_length]
    escaped = truncated.replace("```", "\\`\\`\\`")
    safe_text = "".join(
        char
        for char in escaped
        if not _unicodedata.category(char).startswith("C") or char in "\n\r\t"
    )
    return str(safe_text)
