from widget import parse_range


def test_parse_range_inclusive_basic() -> None:
    assert parse_range("1-3") == [1, 2, 3]


def test_parse_range_inclusive_single() -> None:
    assert parse_range("5-5") == [5]


def test_parse_range_inclusive_zero_start() -> None:
    assert parse_range("0-2") == [0, 1, 2]
