#!/usr/bin/env bash
# Boot the Hammock dashboard for local dogfooding.
#
# Usage:
#   ./scripts/run-hammock.sh             # build SPA, run dashboard (one server)
#   ./scripts/run-hammock.sh --dev       # vite dev (5173) + dashboard (8765),
#                                          frontend hot-reloads
#   ./scripts/run-hammock.sh --build     # just rebuild the SPA, exit
#   ./scripts/run-hammock.sh --help
#
# Env passthrough (all optional):
#   HAMMOCK_ROOT             default ~/.hammock
#   HAMMOCK_PORT             default 8765
#   HAMMOCK_CLAUDE_BINARY    default `claude` from PATH
#   HAMMOCK_FAKE_FIXTURES_DIR  set to use FakeStageRunner instead of real claude
#
# Dashboard auto-spawns the engine driver subprocess when a job is submitted,
# and the per-job MCP server. Nothing else needs to run.

set -euo pipefail

# ----------------------------------------------------------------- paths ----
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
FRONTEND_DIR="$REPO_ROOT/dashboard/frontend"

# --------------------------------------------------------------- args ----
MODE="serve"
case "${1:-}" in
    --help | -h)
        sed -n '2,18p' "$0" | sed 's/^# \?//'
        exit 0
        ;;
    --dev) MODE="dev" ;;
    --build) MODE="build" ;;
    "" | --serve) MODE="serve" ;;
    *)
        echo "Unknown arg: $1" >&2
        echo "Run with --help for usage." >&2
        exit 2
        ;;
esac

# --------------------------------------------------------------- preflight ---
require() {
    if ! command -v "$1" >/dev/null 2>&1; then
        echo "Error: '$1' not on PATH." >&2
        exit 1
    fi
}
require uv
require pnpm

if [[ "$MODE" == "serve" || "$MODE" == "dev" ]]; then
    if ! command -v "${HAMMOCK_CLAUDE_BINARY:-claude}" >/dev/null 2>&1; then
        if [[ -z "${HAMMOCK_FAKE_FIXTURES_DIR:-}" ]]; then
            echo "Warning: claude CLI not found and HAMMOCK_FAKE_FIXTURES_DIR unset."
            echo "         Real-runner mode will fail to spawn jobs."
            echo "         Either install claude or set HAMMOCK_FAKE_FIXTURES_DIR." >&2
        fi
    fi
fi

# --------------------------------------------------------------- frontend ---
build_frontend() {
    echo "==> Installing frontend deps..."
    ( cd "$FRONTEND_DIR" && pnpm install --frozen-lockfile )
    echo "==> Building SPA..."
    ( cd "$FRONTEND_DIR" && pnpm build )
}

# --------------------------------------------------------------- dashboard --
run_dashboard() {
    echo "==> Starting dashboard (port ${HAMMOCK_PORT:-8765})..."
    cd "$REPO_ROOT"
    exec uv run python -m dashboard
}

# --------------------------------------------------------------- modes ----
case "$MODE" in
    build)
        build_frontend
        echo "==> Build complete: $FRONTEND_DIR/dist/"
        ;;

    serve)
        build_frontend
        run_dashboard
        ;;

    dev)
        # Two processes: vite dev server + dashboard backend. Frontend
        # dev server proxies /api and /sse to the dashboard (configured
        # in vite.config.ts), so the user opens http://127.0.0.1:5173
        # for hot-reloaded dev work.
        echo "==> Installing frontend deps..."
        ( cd "$FRONTEND_DIR" && pnpm install --frozen-lockfile )

        # Trap to kill both children on Ctrl+C / exit.
        cleanup() {
            jobs -p | xargs -r kill 2>/dev/null || true
            wait 2>/dev/null || true
        }
        trap cleanup EXIT INT TERM

        echo "==> Starting vite dev (port 5173)..."
        ( cd "$FRONTEND_DIR" && pnpm dev ) &

        echo "==> Starting dashboard backend (port ${HAMMOCK_PORT:-8765})..."
        ( cd "$REPO_ROOT" && uv run python -m dashboard ) &

        cat <<EOF

────────────────────────────────────────────────────────────────────
  Hammock running in dev mode:
    Frontend (hot-reload): http://127.0.0.1:5173
    Dashboard API:         http://127.0.0.1:${HAMMOCK_PORT:-8765}
  Ctrl+C to stop both.
────────────────────────────────────────────────────────────────────

EOF
        wait
        ;;
esac
