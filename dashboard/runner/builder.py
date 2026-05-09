"""Spawn a single workflow-builder claude turn synchronously.

The session is a directory with messages.jsonl + current.yaml. Each
turn:
- assemble a prompt: builder system prompt + current yaml + history + new user text
- run `claude -p <prompt> --output-format json --permission-mode bypassPermissions`
- parse the json response, extract any ```yaml workflow``` block
- return {text, proposed_yaml}

Validation of the proposed yaml against the Workflow schema happens at
the API layer (so we can keep this helper pure-mechanical).
"""

from __future__ import annotations

import json
import logging
import re
import subprocess
from collections.abc import Callable
from pathlib import Path

log = logging.getLogger(__name__)

PROMPTS_DIR = Path(__file__).resolve().parent.parent.parent / "hammock" / "prompts"
BUILDER_PROMPT_PATH = PROMPTS_DIR / "workflow-builder.md"

# Match  ```yaml workflow ... ```  or  ```yaml ... ```  fenced blocks.
# Prefer the explicit `yaml workflow` marker but accept plain `yaml` as a
# fallback so the agent's natural code-fence style still extracts.
_FENCED_RE = re.compile(
    r"```yaml(?:\s+workflow)?\s*\n(.*?)```",
    re.DOTALL | re.IGNORECASE,
)

# Injected for tests so we don't need a real `claude` binary on PATH.
ClaudeRunner = Callable[[list[str], int], "subprocess.CompletedProcess[bytes]"]


def _default_claude_runner(args: list[str], timeout: int) -> subprocess.CompletedProcess[bytes]:
    return subprocess.run(args, capture_output=True, timeout=timeout, check=False)


def _read_jsonl(path: Path) -> list[dict[str, object]]:
    if not path.is_file():
        return []
    out: list[dict[str, object]] = []
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(obj, dict):
            out.append(obj)
    return out


def _format_history(messages: list[dict[str, object]]) -> str:
    if not messages:
        return "_(no prior messages — this is the first turn)_"
    lines: list[str] = []
    for m in messages:
        sender = m.get("from", "?")
        text = m.get("text", "")
        lines.append(f"### {sender}\n\n{text}\n")
    return "\n".join(lines)


def assemble_builder_prompt(
    *,
    builder_template: str,
    current_yaml: str,
    history: list[dict[str, object]],
    user_text: str,
) -> str:
    history_md = _format_history(history)
    return (
        f"""{builder_template}

---

## Current draft yaml

```yaml
{current_yaml.strip() or "# (empty — propose a starting point)"}
```

## Conversation so far

{history_md}

## User's latest message

{user_text}
""".rstrip()
        + "\n"
    )


def extract_proposed_yaml(text: str) -> str | None:
    matches = _FENCED_RE.findall(text or "")
    if not matches:
        return None
    # Last fenced block wins — agent typically ends with the proposal.
    return matches[-1].strip()


def spawn_builder_turn(
    *,
    session_dir: Path,
    user_text: str,
    claude_binary: str = "claude",
    timeout_seconds: int = 120,
    runner: ClaudeRunner | None = None,
) -> dict[str, object]:
    """Run a single builder turn synchronously. Returns:

        {"text": <full markdown>, "proposed_yaml": <str or None>, "raw_result": <str>}

    On error returns text with an explanation and proposed_yaml=None.
    """
    runner = runner or _default_claude_runner

    if not BUILDER_PROMPT_PATH.is_file():
        return {
            "text": "Workflow-builder prompt template missing on the server.",
            "proposed_yaml": None,
            "raw_result": "",
        }

    builder_template = BUILDER_PROMPT_PATH.read_text()
    current_yaml = ""
    yaml_path = session_dir / "current.yaml"
    if yaml_path.is_file():
        current_yaml = yaml_path.read_text()
    messages = _read_jsonl(session_dir / "messages.jsonl")

    full_prompt = assemble_builder_prompt(
        builder_template=builder_template,
        current_yaml=current_yaml,
        history=messages,
        user_text=user_text,
    )

    args = [
        claude_binary,
        "-p",
        full_prompt,
        "--output-format",
        "json",
        "--permission-mode",
        "bypassPermissions",
    ]
    try:
        completed = runner(args, timeout_seconds)
    except subprocess.TimeoutExpired:
        return {
            "text": f"Builder agent timed out after {timeout_seconds}s.",
            "proposed_yaml": None,
            "raw_result": "",
        }
    except FileNotFoundError:
        return {
            "text": f"claude binary {claude_binary!r} not found on PATH.",
            "proposed_yaml": None,
            "raw_result": "",
        }

    if completed.returncode != 0:
        stderr = (completed.stderr or b"").decode(errors="replace").strip()
        return {
            "text": f"Builder agent exited rc={completed.returncode}. stderr: {stderr[:400]}",
            "proposed_yaml": None,
            "raw_result": "",
        }

    raw = (completed.stdout or b"").decode(errors="replace")
    if not raw.strip():
        return {
            "text": "Builder agent returned empty output.",
            "proposed_yaml": None,
            "raw_result": "",
        }

    try:
        obj = json.loads(raw)
    except json.JSONDecodeError:
        # Sometimes claude returns plain text under --output-format json
        # if the harness misbehaves; treat it as the response text.
        return {
            "text": raw.strip(),
            "proposed_yaml": extract_proposed_yaml(raw),
            "raw_result": raw,
        }

    text = ""
    if isinstance(obj, dict):
        # claude --output-format json shape:
        #   {"type": "result", "result": "<assistant text>", ...}
        result = obj.get("result")
        text = result if isinstance(result, str) else json.dumps(obj, indent=2)
    else:
        text = json.dumps(obj)

    return {
        "text": text,
        "proposed_yaml": extract_proposed_yaml(text),
        "raw_result": raw,
    }


__all__ = [
    "BUILDER_PROMPT_PATH",
    "ClaudeRunner",
    "assemble_builder_prompt",
    "extract_proposed_yaml",
    "spawn_builder_turn",
]
