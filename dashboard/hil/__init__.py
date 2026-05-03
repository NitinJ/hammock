"""HIL plane — domain layer.

Per design doc § HIL bridge § HIL lifecycle. Three modules:

- ``state_machine``: pure transition logic, no I/O.
- ``contract``: ``HilContract`` exposes ``get_open_items`` and ``submit_answer``
  as a thin layer over the cache + filesystem.
- ``orphan_sweeper``: cancels all ``awaiting`` HIL items for a stage on restart.
"""

from dashboard.hil.contract import HilContract, HilFilter
from dashboard.hil.orphan_sweeper import OrphanSweeper
from dashboard.hil.state_machine import InvalidTransitionError, transition

__all__ = [
    "HilContract",
    "HilFilter",
    "InvalidTransitionError",
    "OrphanSweeper",
    "transition",
]
