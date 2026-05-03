# dogfood-bug fixture

A synthetic mini-project with one intentional, recorded bug. Used by the
manual Stage 16 dogfood walk-through to exercise hammock end-to-end against
a real (but tiny and disposable) target. Not used by the automated e2e
test (that uses `fake-runs/lifecycle/` for deterministic stage outcomes).

## What it is

A trivial Python package, `widget/`, exposing one function `parse_range()`.
The function has a known off-by-one bug:

```python
def parse_range(s: str) -> list[int]:
    """Parse 'a-b' into the inclusive integer range [a, b]."""
    a, b = s.split("-")
    return list(range(int(a), int(b)))   # <-- bug: should be int(b) + 1
```

The accompanying test `tests/test_parse_range.py` asserts the *correct*
behaviour (inclusive). It fails on a fresh checkout — that failure is the
bug a hammock fix-bug job is meant to find and resolve.

## How the dogfood walk works

1. Initialise the fixture as a real git repo (`scripts/init-dogfood.sh`
   inside the fixture, or run the steps in `runbook.md § Manual dogfood`).
2. Push it to a throwaway GitHub repo (or use `--skip-remote-checks` for
   purely local).
3. `hammock project register <path>`.
4. `hammock job submit --project dogfood-bug --type fix-bug \
       --title "parse_range off-by-one" --request-file prompt.md`.
5. Watch the dashboard at http://127.0.0.1:8765/. Walk through the four
   human-gated stages (design-spec, impl-spec, impl-plan, summary).
6. Confirm the final commit changes line ~3 of `widget/__init__.py` and the
   fix matches what's recorded in `expected-fix.md`.

`expected-fix.md` is the recorded ground truth used to evaluate whether the
real Claude run produced the correct fix.

## Files

- `widget/__init__.py` — the package with the bug.
- `tests/test_parse_range.py` — the failing test that exposes the bug.
- `prompt.md` — the human prompt that the operator pastes into
  `--request-file`.
- `expected-fix.md` — recorded correct fix; for evaluation.
- `pyproject.toml` — minimal project metadata so `pip install -e .` works.
- `CLAUDE.md` — minimal project instructions for the agent.
