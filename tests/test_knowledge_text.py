from math_agent.web.knowledge_text import short_knowledge_rows, short_knowledge_texts


def test_short_knowledge_texts():
    items = [" Fact A ", {"statement": "Theorem 1"}, {"title": "Idea", "body": "hint"}]
    assert short_knowledge_texts(items) == ["Fact A", "Theorem 1", "Idea"]


def test_short_knowledge_rows():
    items = ["Fact A", {"statement": "Theorem 1", "why": "classic"}]
    rows = short_knowledge_rows(items)
    assert rows[0] == {"title": "Fact A", "body": ""}
    assert rows[1]["title"] == "Theorem 1"
    assert rows[1]["body"] == "classic"
