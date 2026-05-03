"""Integration test report schema.

Per design doc § integration-test-report-schema. Produced by the
``run-integration-tests`` stage (Phase 6) and by verify subagents in
the cross-cutting test-isolation pattern.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class TestFailure(BaseModel):
    model_config = ConfigDict(extra="forbid")

    test_name: str = Field(min_length=1)
    file_path: str = Field(min_length=1)
    error_summary: str = Field(min_length=1)


class IntegrationTestReport(BaseModel):
    model_config = ConfigDict(extra="forbid")

    verdict: Literal["passed", "failed", "errored"]
    summary: str = Field(min_length=1)
    test_command: str = Field(min_length=1)
    total_count: int = Field(ge=0)
    passed_count: int = Field(ge=0)
    failed_count: int = Field(ge=0)
    skipped_count: int = Field(ge=0)
    failures: list[TestFailure] = Field(default_factory=list)
    duration_seconds: float = Field(ge=0)
