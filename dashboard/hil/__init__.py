"""HIL plane — Stage 3 v1 cutover.

The v0 contract / state-machine / orphan-sweeper code is gone. v1 HIL
flows through ``engine/v1/hil.submit_hil_answer``; the dashboard's
``api/hil.py`` is a thin wrapper over it.

The remaining v0-shaped modules (``orphan_sweeper.py``,
``state_machine.py``, ``template_registry.py``) are dead code that the
Stage 6 frontend rewrite retires; this PR no longer re-exports them.
"""
