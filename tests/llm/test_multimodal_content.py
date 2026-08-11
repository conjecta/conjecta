from math_agent.llm.base import Message


def test_message_accepts_list_content():
    parts = [
        {"type": "text", "text": "What is in this image?"},
        {"type": "image_url", "image_url": {"url": "data:image/png;base64,AAAA"}},
    ]
    m = Message(role="user", content=parts)
    assert isinstance(m.content, list)
    assert m.content[1]["image_url"]["url"].startswith("data:image/png")


def test_openai_backend_preserves_list_content(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "k")
    from math_agent.llm.openai import OpenAICompatibleBackend
    backend = OpenAICompatibleBackend(
        model="gpt-5.6-sol",
        base_url="https://example.test/v1",
        api_key_env="OPENAI_API_KEY",
    )
    parts = [{"type": "text", "text": "hi"}, {"type": "image_url", "image_url": {"url": "data:image/png;base64,AAAA"}}]
    built = backend._build_api_messages([Message(role="user", content=parts)], system="sys")
    assert built[0] == {"role": "system", "content": "sys"}
    assert built[1]["role"] == "user"
    assert built[1]["content"] == parts  # unchanged, list preserved
