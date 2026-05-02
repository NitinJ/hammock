"""Filesystem watcher — bridges disk into the cache + pub/sub.

Stage 1 ships :mod:`dashboard.watcher.tailer`, which runs ``watchfiles.awatch``
on the hammock root and dispatches each change.
"""

from dashboard.watcher.tailer import CacheChange, run

__all__ = ["CacheChange", "run"]
