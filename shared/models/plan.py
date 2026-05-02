"""Plan / stage-list schemas.

Per design doc § Stage as universal primitive § The expander pattern. A plan
is an ordered list of stage definitions; ``stage-list.yaml`` is the on-disk
form. Expander stages append to it at runtime via the ``append_stages`` MCP
tool.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from shared.models.stage import StageDefinition

# v0: PlanStage is a stage definition under a different name. The split in
# §5.3 of the impl plan is conceptual (compile-time vs run-time view of the
# same shape); a single class is enough.
PlanStage = StageDefinition


class Plan(BaseModel):
    """Ordered stage list — appended by expanders, read by the Job Driver."""

    model_config = ConfigDict(extra="forbid")

    stages: list[StageDefinition] = Field(default_factory=list)
