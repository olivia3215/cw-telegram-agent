import pytest

from utils import normalize_peer_id


@pytest.mark.parametrize(
    "raw, expected",
    [
        (123, 123),
        ("123", 123),
        ("  456  ", 456),
        ("u789", 789),
        ("-321", -321),
    ],
)
def test_normalize_peer_id_happy_path(raw, expected):
    assert normalize_peer_id(raw) == expected


@pytest.mark.parametrize("raw", ["u-123", "abc", "12.3", ""])
def test_normalize_peer_id_rejects_invalid(raw):
    with pytest.raises(ValueError):
        normalize_peer_id(raw)

