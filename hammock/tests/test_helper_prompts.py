"""Helper prompt template existence + structural sanity.

The orchestrator delegates substantial work to five helper Tasks. Each
helper has a prompt template at `hammock/prompts/helpers/<name>.md`.
We assert each template is on disk, declares its inputs, declares its
constraints (file-write hard rule), and ends with a `## Result`
contract section.
"""

from __future__ import annotations

from pathlib import Path

import pytest

HELPERS_DIR = Path(__file__).resolve().parent.parent / "prompts" / "helpers"

EXPECTED_HELPERS = (
    "prepare-node-input",
    "process-expansion",
    "interpret-message",
    "prepare-revision-respawn",
    "synthesize-status",
)


@pytest.mark.parametrize("name", EXPECTED_HELPERS)
def test_helper_template_exists(name: str) -> None:
    p = HELPERS_DIR / f"{name}.md"
    assert p.is_file(), f"helper template missing: {p}"
    text = p.read_text()
    # Identity statement
    assert name in text, f"{name}: identity statement missing"
    # Inputs section
    assert "## Inputs" in text or "Inputs" in text, f"{name}: Inputs section missing"
    # Output contract
    assert "## Result" in text, f"{name}: ## Result contract section missing"
    # Constraints (the helper-write hard rule)
    assert "Constraints" in text, f"{name}: Constraints section missing"


def test_helpers_forbid_writing_to_orchestrator_state() -> None:
    """Every helper must explicitly state it cannot write
    `orchestrator_state.json`, `job.md`, `control.md`, or
    `orchestrator_messages.jsonl` — those are the orchestrator's
    exclusive surface."""
    for name in EXPECTED_HELPERS:
        text = (HELPERS_DIR / f"{name}.md").read_text()
        assert "orchestrator_state.json" in text, (
            f"{name}: missing forbid-write for orchestrator_state.json"
        )
        assert "job.md" in text, f"{name}: missing forbid-write for job.md"
        assert "control.md" in text, f"{name}: missing forbid-write for control.md"
        assert "orchestrator_messages.jsonl" in text, (
            f"{name}: missing forbid-write for orchestrator_messages.jsonl"
        )


def test_prepare_node_input_lists_required_inputs() -> None:
    text = (HELPERS_DIR / "prepare-node-input.md").read_text()
    for var in ("$NODE_ID", "$JOB_DIR", "$DEP_NODE_IDS", "$NODE_PROMPT_TEMPLATE"):
        assert var in text, f"prepare-node-input: missing input {var}"
    # Result shape
    assert '{"ok": true}' in text


def test_process_expansion_lists_required_inputs() -> None:
    text = (HELPERS_DIR / "process-expansion.md").read_text()
    assert "$EXPANDER_ID" in text
    assert "$JOB_DIR" in text
    # Returned shape includes expanded_nodes map
    assert "expanded_nodes" in text
    assert "parent_expander" in text


def test_interpret_message_action_set() -> None:
    text = (HELPERS_DIR / "interpret-message.md").read_text()
    for action in ("skip", "abort", "rerun", "add-instructions", "status", "other"):
        assert action in text, f"interpret-message: missing action {action!r}"
    assert "$OPERATOR_MESSAGE_TEXT" in text
    assert "$BRIEF_STATE_SUMMARY" in text


def test_prepare_revision_respawn_inputs() -> None:
    text = (HELPERS_DIR / "prepare-revision-respawn.md").read_text()
    assert "$NODE_ID" in text
    assert "$REVIEWER_COMMENT" in text
    assert "$JOB_DIR" in text
    # Must mention input.md + prompt.md as the only writable surface.
    assert "input.md" in text
    assert "prompt.md" in text


def test_synthesize_status_inputs() -> None:
    text = (HELPERS_DIR / "synthesize-status.md").read_text()
    assert "$STATE_JSON_SNAPSHOT" in text
    assert "response_text" in text
