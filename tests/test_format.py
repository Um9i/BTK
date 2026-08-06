import pytest

from btk.bot.format import num2short, short2num


@pytest.mark.parametrize(
    ("num", "expected"),
    [
        (0, "0"),
        (9999, "9999"),
        (10000, "10k"),
        (12345, "12.3k"),
        (10_000_000, "10m"),
        (-25000, "-25k"),
    ],
)
def test_num2short(num: int, expected: str) -> None:
    assert num2short(num) == expected


@pytest.mark.parametrize(
    ("short", "expected"),
    [
        ("10k", 10000),
        ("1.5m", 1500000),
        ("1,000", 1000),
        ("500", 500),
    ],
)
def test_short2num(short: str, expected: int) -> None:
    assert short2num(short) == expected


def test_short2num_invalid() -> None:
    with pytest.raises(ValueError):
        short2num("not-a-number")
