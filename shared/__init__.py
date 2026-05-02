"""Shared cross-process contract surface.

Imported by both ``dashboard/`` and ``job_driver/``. Nothing in this package
imports from either of them — the dependency direction is one-way.
"""

__all__ = ["__version__"]

__version__ = "0.0.0"
