# dogfood-widget

A throw-away project used by the hammock Stage 16 dogfood walk-through.

## Layout

- `widget/__init__.py` — single-module package; one public function.
- `tests/test_parse_range.py` — pytest suite; the failing tests encode the
  documented contract.

## Conventions

- Python 3.12+, type hints required.
- pytest for tests; run with `pytest tests/`.
- Conventional Commits.
