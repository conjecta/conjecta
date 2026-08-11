from math_agent.agent.supervisor import build_first_user_content
from math_agent.web.attachments import ATTACHMENT_ONLY_PROBLEM


def test_no_attachments_returns_plain_string():
    assert build_first_user_content("solve x", None) == "solve x"
    assert build_first_user_content("solve x", []) == "solve x"


def test_attachments_produce_multimodal_parts():
    atts = [{"type": "image_url", "image_url": {"url": "data:image/png;base64,AAAA"}}]
    out = build_first_user_content("solve x", atts)
    assert isinstance(out, list)
    assert out[0] == {"type": "text", "text": "solve x"}
    assert out[1] == atts[0]


def test_attachment_only_prompt_gets_clarification():
    atts = [{"type": "image_url", "image_url": {"url": "data:image/png;base64,AAAA"}}]
    out = build_first_user_content(ATTACHMENT_ONLY_PROBLEM, atts)
    assert isinstance(out, list)
    assert out[0] == {
        "type": "text",
        "text": ATTACHMENT_ONLY_PROBLEM + "\n\n请依据下方附件中的内容进行解答。",
    }
    assert out[1] == atts[0]
