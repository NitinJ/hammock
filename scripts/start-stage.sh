#!/usr/bin/env bash
# Spin up a parallel-stage worktree and drop into a Claude Code session for it.
#
# Usage (run from ~/workspace/, or anywhere — paths are absolute):
#   ./hammock/scripts/start-stage.sh <stage-number>
#
# Examples:
#   ./hammock/scripts/start-stage.sh 8
#   ./hammock/scripts/start-stage.sh 11
#
# What it does:
#   1. Resolves the worktree path: ~/workspace/hammock-stage-<NN>  (zero-padded).
#   2. Creates the worktree off origin/main if it doesn't already exist.
#      Re-running with the same N is safe — it just enters the existing worktree.
#   3. Runs `uv sync --dev` in the worktree.
#   4. Substitutes <NN> in docs/prompts/stage-prompt.txt, prepends the worktree
#      path so the agent knows where to operate, then `exec`s `claude` from
#      ~/workspace with that prompt as the first user message. The current
#      terminal becomes the Claude Code session (interactive).
#
# CWD policy:
#   The script's own cwd stays at ~/workspace from the very start (after
#   preflight) so claude inherits ~/workspace as its project directory —
#   this is where the user's claude config (CLAUDE.md, .claude/, etc.) lives.
#   Worktree operations are scoped to subshells so the parent cwd never
#   drifts. The agent gets the worktree path explicitly via the prompt
#   header.
#
# Exits non-zero before launching Claude on any setup error.

set -euo pipefail

# ------------------------------------------------------------------ args ----
if [[ $# -lt 1 ]]; then
    cat <<EOF >&2
Usage: $0 <stage-number>

Examples:
  $0 8       # → ~/workspace/hammock-stage-08
  $0 11      # → ~/workspace/hammock-stage-11
EOF
    exit 2
fi

N="$1"
if ! [[ "$N" =~ ^[0-9]+$ ]]; then
    echo "Error: stage number must be a non-negative integer, got '$N'" >&2
    exit 2
fi
NN="$(printf '%02d' "$N")"

# ----------------------------------------------------------------- paths ----
HAMMOCK_DIR="${HAMMOCK_DIR:-$HOME/workspace/hammock}"
WORKTREE_DIR="$HOME/workspace/hammock-stage-$NN"
PROMPT_TEMPLATE="$HAMMOCK_DIR/docs/prompts/stage-prompt.txt"

# --------------------------------------------------------------- preflight ---
if [[ ! -d "$HAMMOCK_DIR/.git" ]]; then
    echo "Error: $HAMMOCK_DIR is not a git repository." >&2
    echo "Set HAMMOCK_DIR if your hammock checkout lives elsewhere." >&2
    exit 1
fi

if [[ ! -f "$PROMPT_TEMPLATE" ]]; then
    echo "Error: prompt template not found at $PROMPT_TEMPLATE" >&2
    exit 1
fi

if ! command -v claude >/dev/null 2>&1; then
    echo "Error: 'claude' CLI not found in PATH." >&2
    exit 1
fi

if ! command -v uv >/dev/null 2>&1; then
    echo "Error: 'uv' CLI not found in PATH." >&2
    exit 1
fi

# ----------------------------------------------------------------- cwd ------
# Anchor cwd at ~/workspace immediately. Every subsequent command in this
# script either uses an absolute path or runs in a subshell — the parent
# shell never leaves ~/workspace, so the final `exec claude` inherits it.
if [[ ! -d "$HOME/workspace" ]]; then
    echo "Error: $HOME/workspace does not exist." >&2
    exit 1
fi
cd "$HOME/workspace"

# --------------------------------------------------------------- worktree ---
if [[ -d "$WORKTREE_DIR" ]]; then
    echo "Worktree already exists at $WORKTREE_DIR — reusing."
else
    echo "Fetching latest main..."
    git -C "$HAMMOCK_DIR" fetch origin main

    echo "Creating worktree: $WORKTREE_DIR (off origin/main)"
    git -C "$HAMMOCK_DIR" worktree add "$WORKTREE_DIR" origin/main
fi

# --------------------------------------------------------------- uv sync ----
echo "Syncing dependencies in $WORKTREE_DIR (uv sync --dev)..."
( cd "$WORKTREE_DIR" && uv sync --dev )

# --------------------------------------------------------------- prompt -----
# Substitute <NN> and prepend the worktree path so the agent knows to operate
# inside the worktree even though Claude's cwd will be ~/workspace.
TEMPLATE_BODY="$(sed "s/<NN>/$NN/g" "$PROMPT_TEMPLATE")"

PROMPT="$(cat <<HEADER
WORKING DIRECTORY FOR THIS STAGE: $WORKTREE_DIR

Your shell cwd is ~/workspace (the user's main work directory). The hammock
stage worktree lives at the path above. For every command and file operation
in this session, use absolute paths under that worktree, or prefix bash
calls with \`cd $WORKTREE_DIR && <cmd>\`. Do NOT touch the sibling
\`hammock\` checkout at \$HOME/workspace/hammock — that is the human's
main worktree.

The deps have already been synced via \`uv sync --dev\` inside the worktree.

────────────────────────────────────────────────────────────────────────────

$TEMPLATE_BODY
HEADER
)"

cat <<EOF

────────────────────────────────────────────────────────────────────────────
  Stage $NN ready.
  Worktree:    $WORKTREE_DIR
  Branch:      will be created by the agent (feat/stage-$NN-<short-name>)
  Prompt:      $PROMPT_TEMPLATE (with <NN>=$NN substituted, +worktree header)
  Claude cwd:  $HOME/workspace
  Launching:   claude  (interactive)
────────────────────────────────────────────────────────────────────────────

EOF

# Sanity: pwd should still be ~/workspace (we never `cd` outside subshells).
if [[ "$PWD" != "$HOME/workspace" ]]; then
    echo "Internal error: pwd drifted to $PWD; expected $HOME/workspace" >&2
    exit 1
fi

# Replace the shell with claude. Claude inherits cwd=~/workspace, picking up
# the user's claude config there. The agent reads its target worktree from
# the WORKING DIRECTORY header in the prompt.
exec claude "$PROMPT"
