"""UI template registry — per-project-first resolution.

Per design doc § Presentation plane § Form pipeline and template registry.

Resolution order:
  1. ``<project_repo_root>/.hammock/ui-templates/<name>.json``  (tunable)
  2. ``<root>/ui-templates/<name>.json``                         (kernel default)

Override semantics: overlay may modify ``instructions``, ``description``, and
``fields``, but must not change ``hil_kinds`` (that would alter the kind
contract, which is kernel).
"""

from __future__ import annotations

from pathlib import Path

from shared.models.presentation import UiTemplate
from shared.paths import ui_templates_dir


class TemplateNotFoundError(Exception):
    """Raised when a template cannot be found in either global or project dirs."""


class TemplateKindConflictError(Exception):
    """Raised when a per-project override attempts to change ``hil_kinds``."""


class TemplateRegistry:
    """Resolves named UI templates with per-project-first semantics.

    Parameters
    ----------
    root:
        Hammock root directory (``~/.hammock`` by default).  Global templates
        are read from ``<root>/ui-templates/``.
    """

    def __init__(self, *, root: Path) -> None:
        self._global_dir: Path = ui_templates_dir(root)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def resolve(
        self,
        name: str,
        *,
        project_repo: Path | None = None,
    ) -> UiTemplate:
        """Return the resolved template for *name*.

        If *project_repo* is given and a per-project override exists at
        ``<project_repo>/.hammock/ui-templates/<name>.json``, it is loaded and
        merged over the global default.  The override may not change
        ``hil_kinds`` — doing so raises :class:`TemplateKindConflictError`.

        Raises :class:`TemplateNotFoundError` if no global template exists.
        """
        raise NotImplementedError

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _load(self, path: Path) -> UiTemplate:
        """Parse and validate a template JSON file."""
        raise NotImplementedError

    def _merge(self, base: UiTemplate, override: UiTemplate) -> UiTemplate:
        """Overlay *override* onto *base*, enforcing kernel invariants.

        Raises :class:`TemplateKindConflictError` if ``override.hil_kinds``
        differs from ``base.hil_kinds`` (and override.hil_kinds is not None).
        """
        raise NotImplementedError
