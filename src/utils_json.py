import json
import re

THOUGHT_BLOCK_RE = re.compile(r"<thought>.*?</thought>", flags=re.DOTALL | re.IGNORECASE)
TRUNCATED_THOUGHT_BLOCK_RE = re.compile(r"<thought>.*", flags=re.DOTALL | re.IGNORECASE)
MARKDOWN_JSON_BLOCK_RE = re.compile(
    r"```(?:json|python)?\s*(.*?)\s*```", flags=re.DOTALL | re.IGNORECASE
)


def _repair_json(json_str: str) -> str:
    """Simple JSON repair for truncated EOF strings"""
    stack = []
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
    """Extracts JSON from an LLM response, stripping markdown and <thought> tags.
    Handles multiple markdown code blocks by trying to parse each one until a valid JSON dict/list is found.
    Handles truncated JSON gracefully using simple stack-based repair."""

    content = THOUGHT_BLOCK_RE.sub("", content)
    # Handle cases where <thought> tag is opened but NOT closed (truncation)
    content = TRUNCATED_THOUGHT_BLOCK_RE.sub("", content)

    # 1. Try to find all markdown blocks and parse them.
    blocks = MARKDOWN_JSON_BLOCK_RE.findall(content)
    for block in blocks:
        repaired = _repair_json(block.strip())
        try:
            parsed = json.loads(repaired)
            if isinstance(parsed, (dict, list)):
                return repaired
        except json.JSONDecodeError:
            continue

    # 2. If no valid block, fallback to finding the outermost { ... }
    start_idx = content.find("{")
    if start_idx != -1:
        json_str = content[start_idx:].strip()
        repaired = _repair_json(json_str)
        try:
            parsed = json.loads(repaired)
            if isinstance(parsed, (dict, list)):
                return repaired
        except json.JSONDecodeError:
            pass
        # Return repaired anyway as a best-effort
        return repaired

    # 3. Ultimate fallback
    return _repair_json(content.strip())
