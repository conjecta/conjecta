from pathlib import Path

WEB = Path(__file__).resolve().parents[2] / "web"
BANNED = ["team.html", "Fudan", "复旦", "Shanghai Center for Mathematical Sciences", "上海数学中心"]


def test_no_banned_strings_in_web():
    offenders = []
    for path in WEB.rglob("*"):
        if path.suffix.lower() not in {".html", ".js", ".css"}:
            continue
        text = path.read_text(encoding="utf-8", errors="ignore")
        for term in BANNED:
            if term in text:
                offenders.append(f"{path.name}: {term}")
    assert offenders == [], offenders


def test_team_html_removed():
    assert not (WEB / "team.html").exists()
