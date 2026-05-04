#!/usr/bin/env bash
# validate-stage-exit.sh — Hammock Stop hook (hammock-internal kernel)
#
# Called by Claude Code's Stop hook mechanism before allowing a session to
# exit. Validates that all required stage outputs exist in the job dir.
#
# Environment variables (set by RealStageRunner):
#   HAMMOCK_JOB_DIR               path to the job's storage directory
#   HAMMOCK_STAGE_REQUIRED_OUTPUTS newline-separated list of relative paths
#
# Exit codes:
#   0  — all outputs present; session may exit
#   2  — one or more outputs missing; blocks exit with structured feedback
#         (Claude Code shows stdout to the agent so it can fix the issue)
#
# If HAMMOCK_JOB_DIR is unset (e.g., non-hammock session), exit 0 immediately.

set -euo pipefail

JOB_DIR="${HAMMOCK_JOB_DIR:-}"
REQUIRED_OUTPUTS="${HAMMOCK_STAGE_REQUIRED_OUTPUTS:-}"

if [[ -z "$JOB_DIR" ]]; then
    exit 0
fi

missing=()

while IFS= read -r rel_path; do
    [[ -z "$rel_path" ]] && continue
    if [[ ! -f "$JOB_DIR/$rel_path" ]]; then
        missing+=("$rel_path")
    fi
done <<< "$REQUIRED_OUTPUTS"

if [[ ${#missing[@]} -gt 0 ]]; then
    # Claude Code surfaces hook *stderr* (not stdout) as feedback to the
    # agent; writing to stdout produces "No stderr output" with no detail.
    {
        echo "Stage exit blocked: required outputs not found in job dir."
        echo ""
        echo "Job dir (write declared outputs here, NOT in the working directory):"
        echo "  $JOB_DIR"
        echo ""
        echo "Missing files:"
        for f in "${missing[@]}"; do
            echo "  - $JOB_DIR/$f"
        done
        echo ""
        echo "Create each missing file at the absolute path shown above and exit again."
    } >&2
    exit 2
fi

exit 0
