from math_agent.search.text_retrieval import lexical_score, multilingual_tokens, query_terms


def test_multilingual_tokens_include_cjk_phrases_and_lean_identifiers():
    tokens = multilingual_tokens("证明偶数平方可整除，使用 Nat.dvd_mul")

    assert "偶数" in tokens
    assert "平方" in tokens
    assert "nat" in tokens
    assert "dvd_mul" in tokens


def test_lexical_score_prefers_relevant_chinese_document():
    query = "证明偶数的平方能被四整除"

    assert lexical_score(query, "偶数平方可以被四整除") > lexical_score(
        query, "讨论无穷多个素数"
    )


def test_query_terms_are_bounded_and_drop_generic_words():
    terms = query_terms("请证明一个偶数平方整除问题", limit=4)

    assert len(terms) == 4
    assert "证明" not in terms
