"""In-memory state layer — typed cache + scoped pub/sub.

This package owns the dashboard's read-side. Both the FastAPI routes (Stage 9+)
and the SSE handlers (Stage 10) read from the cache; the watcher (Stage 1)
writes into it via ``apply_change``.

Nothing here imports from ``api/``. The Domain/Transport split holds at the
import-direction level; CI will enforce it once the import-linter rule lands
in Stage 8.
"""

from dashboard.state.cache import Cache, ChangeKind, ClassifiedPath, classify_path
from dashboard.state.pubsub import InProcessPubSub, PubSubSubscription

__all__ = [
    "Cache",
    "ChangeKind",
    "ClassifiedPath",
    "InProcessPubSub",
    "PubSubSubscription",
    "classify_path",
]
