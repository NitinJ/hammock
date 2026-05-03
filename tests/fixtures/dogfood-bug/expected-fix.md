# Expected fix — `widget.parse_range`

The bug is in `widget/__init__.py`:

```diff
-    return list(range(int(a), int(b)))
+    return list(range(int(a), int(b) + 1))
```

`range(a, b)` is half-open in Python; the documented contract says the
returned range is **inclusive**, so the upper bound must be `b + 1`.

Acceptance:

- `tests/test_parse_range.py` passes without modification (all three cases).
- No other production source file is changed.
- Commit message is in conventional-commits style and references the bug
  (e.g. `fix(widget): parse_range now produces inclusive range`).
- PR description names the contract (inclusive) and the off-by-one cause.
