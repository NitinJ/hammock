"""Plan Compiler — turns a (project, job_type, title, prompt) into a job dir.

Per design doc § Plan Compiler. Deterministic Python; no LLM calls in v0.
Pipeline: load global template → modify-only deep-merge per-project override →
bind params (``${job.slug}``, ``${job.title}``, …) → Pydantic-validate +
structural validation rules → atomically write ``prompt.md``,
``stage-list.yaml``, ``job.json`` → return job slug.
"""

from dashboard.compiler.compile import (
    CompileFailure,
    CompileResult,
    CompileSuccess,
    compile_job,
)

__all__ = ["CompileFailure", "CompileResult", "CompileSuccess", "compile_job"]
