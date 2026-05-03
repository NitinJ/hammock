`widget.parse_range("1-3")` returns `[1, 2]` instead of the expected `[1, 2, 3]`.
The function is documented as producing the **inclusive** range from `a` to `b`,
and the existing tests in `tests/test_parse_range.py` (which were
deliberately written to encode the documented contract) fail on a fresh
checkout. Find and fix the off-by-one bug; do not change the documented
contract; the existing tests must pass without modification.
