import pytest

from api.products import _build_product_search_conditions


@pytest.mark.parametrize(
    "search_text, expected_terms",
    [
        ("maheswari saree", ["maheswari", "saree"]),
        ("Maheswari", ["maheswari"]),
        ("   silk   saree  ", ["silk", "saree"]),
    ],
)
def test_build_product_search_conditions_uses_word_terms(search_text, expected_terms):
    conditions = _build_product_search_conditions(search_text)

    assert conditions[0] == " ".join(search_text.strip().split()).lower()
    assert conditions[1:] == expected_terms


def test_build_product_search_conditions_keeps_display_id_match():
    conditions = _build_product_search_conditions("PROD-123")

    assert conditions[0] == "prod-123"
    assert conditions[1:] == ["prod-123"]


def test_exact_name_match_takes_priority_before_word_search():
    exact_match, phrase_match, all_words_match, any_words_match = __import__("api.products", fromlist=["_build_product_search_filters"])._build_product_search_filters("shop 1 product 1")

    assert exact_match is not None
    assert phrase_match is not None
    assert all_words_match is not None
    assert any_words_match is not None

    exact_sql = str(exact_match)
    phrase_sql = str(phrase_match)
    all_words_sql = str(all_words_match)
    any_words_sql = str(any_words_match)

    assert "lower(products.name)" in exact_sql
    assert "lower(products.display_id)" in exact_sql
    assert "like" in phrase_sql.lower()
    assert "and" in all_words_sql.lower()
    assert "or" in any_words_sql.lower()
