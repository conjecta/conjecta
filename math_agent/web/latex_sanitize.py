"""Lightweight LaTeX sanitization for model-generated answers."""

# Common broken escape patterns seen in model output.
# Each tuple is (bad_token, fixed_token). A token is only replaced when it is
# not preceded by a backslash or a letter.
_LATEX_HARD_REPLACEMENTS: list[tuple[str, str]] = [
    ("igl(", "\\bigl("),
    ("iglr(", "\\biglr("),
    ("igr)", "\\bigr)"),
    ("igr\\\\", "\\bigr\\\\"),
    ("igl\\\\", "\\bigl\\\\"),
    ("sum_", "\\sum_"),
    ("prod_", "\\prod_"),
    ("inf_", "\\inf_"),
    ("sup_", "\\sup_"),
    ("lim_", "\\lim_"),
    ("frac{", "\\frac{"),
    ("mu_", "\\mu_"),
    ("operatorname{", "\\operatorname{"),
    ("mathbb{", "\\mathbb{"),
    ("mathcal{", "\\mathcal{"),
    ("mathfrak{", "\\mathfrak{"),
]


def _replace_token(text: str, old: str, new: str) -> str:
    """Replace occurrences of `old` with `new` only when not preceded by a backslash or letter."""
    result = []
    i = 0
    while i < len(text):
        if text.startswith(old, i):
            prev = text[i - 1] if i > 0 else ""
            if prev not in "\\abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ":
                result.append(new)
                i += len(old)
                continue
        result.append(text[i])
        i += 1
    return "".join(result)


def sanitize_latex_answer(text: str) -> str:
    """Apply hard-rule fixes to common model LaTeX formatting issues."""
    if not text:
        return text
    # A provider or Python string boundary can occasionally decode ``\adots``
    # as the ASCII bell escape. In ordinary answer prose this is almost always
    # an intended ellipsis. Drop other C0 controls while preserving layout.
    text = text.replace("\x07dots", "\\dots")
    text = "".join(
        char for char in text if char in "\n\t" or ord(char) >= 32
    )
    for old, new in _LATEX_HARD_REPLACEMENTS:
        text = _replace_token(text, old, new)
    return text
