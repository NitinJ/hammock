"""End-to-end tests for Hammock v1 (per docs/hammock-design-patch.md).

Six progressive YAMLs (T1..T6); each adds one capability. T6 is the full
fix-bug workflow. The test harness is parameterised over the YAML path so
the same code runs all six stages.

These tests are opt-in (require real Claude + real GitHub); they skip
unless ``HAMMOCK_E2E_REAL_CLAUDE=1``.
"""
