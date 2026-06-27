"""AI agent functions using litellm.

Replaces the previous pydantic-ai agents with direct litellm calls.
"""

from pathlib import Path

from src.config import settings


def _load_file_content(filepath: str) -> str:
    path = Path(filepath)
    if path.exists():
        return path.read_text(encoding="utf-8")
    return ""


def _get_system_context() -> str:
    """Injects global context from ALL_SPEC.md and conventions.md if available."""
    context = []
    docs_dir = Path(settings.paths.documents_dir)
    structured_spec_path = docs_dir / "ALL_SPEC_STRUCTURED.md"
    raw_spec_path = docs_dir / "ALL_SPEC.md"

    if structured_spec_path.exists():
        content = structured_spec_path.read_text(encoding="utf-8")
        context.append(f"### Project Specifications (Structured)\n{content}")
    elif raw_spec_path.exists():
        content = raw_spec_path.read_text(encoding="utf-8")
        context.append(f"### Project Specifications (Raw)\n{content}")

    conventions_path = Path(settings.paths.documents_dir) / "conventions.md"
    if conventions_path.exists():
        content = conventions_path.read_text(encoding="utf-8")
        context.append(f"### Coding Conventions\n{content}")

    return "\n\n".join(context)


async def get_manager_response(question: str, enhanced_context: str) -> str:
    """Get a response from the Manager agent using litellm."""
    import litellm

    system_prompt = settings.read_template(
        "MANAGER_INSTRUCTION.md",
        default=(
            "You are a Senior Technical Project Manager and Debugging Mentor. "
            "When answering questions from the developer (Jules):\n"
            "1. Focus on ROOT CAUSE ANALYSIS - help identify WHY problems occur\n"
            "2. Guide systematic investigation - suggest specific files or debugging steps\n"
            "3. Discourage trial-and-error - promote understanding before fixing\n"
            "4. Be analytical and educational - help Jules become a better problem solver\n"
            "Answer questions accurately, concisely, and with clear reasoning."
        ),
    )

    global_context = _get_system_context()
    if global_context:
        system_prompt = f"{system_prompt}\n\n## Project Context\n{global_context}"

    response = await litellm.acompletion(
        model=_resolve_model(settings.agents.auditor_model),
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": enhanced_context},
        ],
        temperature=0.3,
    )
    return str(response.choices[0].message.content)


def _resolve_model(model_name: str) -> str:
    """Resolve model name. Handles openrouter/ prefix for litellm."""
    if model_name.startswith("openrouter/"):
        return model_name  # litellm handles openrouter/ prefix natively
    if model_name.startswith("gemini/"):
        return model_name.replace("gemini/", "", 1)
    return model_name
