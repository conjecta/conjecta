from math_agent.text_utils import extract_fenced_code, extract_lean_code, parse_json_blob
from math_agent.web.latex_sanitize import sanitize_latex_answer


def test_extract_lean_fence() -> None:
    text = "some prose\n```lean\ntheorem x : True := sorry\n```"
    assert extract_fenced_code(text, "lean") == "theorem x : True := sorry"


def test_extract_any_fence() -> None:
    text = "```\nplain code\n```"
    assert extract_fenced_code(text) == "plain code"


def test_extract_missing_fence() -> None:
    assert extract_fenced_code("no fence") is None


def test_parse_json_blob_with_fence() -> None:
    text = "```json\n{\"a\": 1}\n```"
    assert parse_json_blob(text) == {"a": 1}


def test_parse_json_blob_with_junk() -> None:
    text = "prefix {\"a\": 1} suffix"
    assert parse_json_blob(text) == {"a": 1}


def test_parse_json_blob_invalid() -> None:
    assert parse_json_blob("no json here") is None


def test_extract_lean_code() -> None:
    text = "```lean4\ntheorem t : True := trivial\n```"
    assert extract_lean_code(text) == "theorem t : True := trivial"


def test_extract_lean_code_no_fence() -> None:
    text = "theorem t : True := trivial"
    assert extract_lean_code(text) == "theorem t : True := trivial"


def test_latex_sanitizer_repairs_bell_escaped_ellipsis_and_drops_controls() -> None:
    assert sanitize_latex_answer("1,2,\x07dots,2n\x01") == "1,2,\\dots,2n"
