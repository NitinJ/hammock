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
        global_path = self._global_dir / f"{name}.json"
        if not global_path.exists():
            raise TemplateNotFoundError(
                f"Template {name!r} not found at {global_path}"
            )

        base = self._load(global_path)

        if project_repo is None:
            return base

        override_path = project_repo / ".hammock" / "ui-templates" / f"{name}.json"
        if not override_path.exists():
            return base

        override = self._load(override_path)
        return self._merge(base, override)

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _load(self, path: Path) -> UiTemplate:
        """Parse and validate a template JSON file."""
        return UiTemplate.model_validate_json(path.read_text())

    def _merge(self, base: UiTemplate, override: UiTemplate) -> UiTemplate:
        """Overlay *override* onto *base*, enforcing kernel invariants.

        Raises :class:`TemplateKindConflictError` if ``override.hil_kinds``
        differs from ``base.hil_kinds`` (and override.hil_kinds is not None).
        """
        if override.hil_kinds is not None and override.hil_kinds != base.hil_kinds:
            raise TemplateKindConflictError(
                f"Override must not change hil_kinds: "
                f"base={base.hil_kinds!r}, override={override.hil_kinds!r}"
            )

        return base.model_copy(
            update={
                "description": override.description if override.description is not None else base.description,
                "instructions": override.instructions if override.instructions is not None else base.instructions,
                "fields": override.fields if override.fields is not None else base.fields,
                # hil_kinds: keep base value (override may not change it)
                "hil_kinds": base.hil_kinds,
            }
        )
