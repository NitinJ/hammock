"""Disk-state layer — path classification + scoped pub/sub.

Per impl-patch §Stage 3: there is no in-memory cache. The watcher
classifies paths via ``dashboard.state.classify`` and publishes
PathChange messages over ``dashboard.state.pubsub``. Subscribers read
disk on demand to materialize responses.

Nothing here imports from ``dashboard.api/``. Domain/Transport split
holds at the import-direction level.
"""

from dashboard.state.classify import (
    ChangeKind,
    ClassifiedPath,
    classify_path,
    scopes_for,
)
from dashboard.state.pubsub import InProcessPubSub, PubSubSubscription

__all__ = [
    "ChangeKind",
    "ClassifiedPath",
    "InProcessPubSub",
    "PubSubSubscription",
    "classify_path",
    "scopes_for",
]
