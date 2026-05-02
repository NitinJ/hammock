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
#   4. Substitutes <NN> in docs/prompts/stage-prompt.txt to produce the initial
#      prompt, then `exec`s `claude` with that prompt as the first user message.
#      The current terminal becomes the Claude Code session (interactive).
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

# --------------------------------------------------------------- worktree ---
if [[ -d "$WORKTREE_DIR" ]]; then
    echo "Worktree already exists at $WORKTREE_DIR — reusing."
else
    echo "Fetching latest main..."
    git -C "$HAMMOCK_DIR" fetch origin main

    echo "Creating worktree: $WORKTREE_DIR (off origin/main)"
    git -C "$HAMMOCK_DIR" worktree add "$WORKTREE_DIR" origin/main
fi

cd "$WORKTREE_DIR"

# --------------------------------------------------------------- uv sync ----
echo "Syncing dependencies (uv sync --dev)..."
uv sync --dev

# --------------------------------------------------------------- prompt -----
# Substitute <NN> in the prompt template; pass the result as Claude's first
# user message.
PROMPT="$(sed "s/<NN>/$NN/g" "$PROMPT_TEMPLATE")"

cat <<EOF

────────────────────────────────────────────────────────────────────────────
  Stage $NN ready.
  Worktree:    $WORKTREE_DIR
  Branch:      will be created by the agent (feat/stage-$NN-<short-name>)
  Prompt:      $PROMPT_TEMPLATE (with <NN>=$NN substituted)
  Launching:   claude  (interactive)
────────────────────────────────────────────────────────────────────────────

EOF

# `exec` replaces the shell with claude so Ctrl-C, signals, and the user's
# interactive session all behave naturally.
exec claude "$PROMPT"
