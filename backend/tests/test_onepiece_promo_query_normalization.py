import pytest

from app.search_v2.normalization import normalize_onepiece_collector_number


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("P-150", "p-150"),
        ("P150", "p-150"),
        ("P 150", "p-150"),
        ("p_150", "p-150"),
        ("OP05-119", "op05-119"),
        ("OP05119", "op05-119"),
        ("OP 05 119", "op05-119"),
    ],
)
def test_onepiece_collector_input_variants_normalize_to_exact_identity(raw, expected):
    assert normalize_onepiece_collector_number(raw) == expected
