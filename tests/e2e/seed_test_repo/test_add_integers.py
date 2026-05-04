"""Tests for ``add_integers``."""

from __future__ import annotations

import pytest

from add_integers import add_integers


def test_no_args_returns_zero() -> None:
    assert add_integers() == 0


@pytest.mark.parametrize(
    ("inputs", "expected"),
    [
        ((1,), 1),
        ((1, 2, 3), 6),
        ((-1, 1), 0),
        ((10, -5, 2), 7),
    ],
)
def test_sums_inputs(inputs: tuple[int, ...], expected: int) -> None:
    assert add_integers(*inputs) == expected
