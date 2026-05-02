"""The dashboard process — long-lived FastAPI app + watchfiles + supervisors.

Stage 1 ships the storage layer (``state/``) and the watcher (``watcher/``);
the FastAPI shell lands in Stage 8.
"""
