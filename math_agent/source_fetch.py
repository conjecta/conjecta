"""Fetch and extract text from URLs referenced in user prompts."""

from __future__ import annotations

import io
import logging
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from html import unescape

from math_agent.net_safety import UnsafeFetchURL, fetch_public_url

log = logging.getLogger("math_agent.source_fetch")

URL_PATTERN = re.compile(r"https?://[^\s<>\"')\]]+", re.IGNORECASE)
ARXIV_ID_PATTERN = re.compile(
    r"(?:arxiv\.org/(?:abs|pdf)/|arXiv:)(\d{4}\.\d{4,5})(?:v\d+)?",
    re.IGNORECASE,
)

DEFAULT_TIMEOUT_SECONDS = 120.0
DEFAULT_MAX_CHARS = 40_000
SOURCE_HTTP_MAX_BYTES = 4 * 1024 * 1024
_ARXIV_API = "https://export.arxiv.org/api/query?id_list={arxiv_id}"


@dataclass(frozen=True)
class FetchedSource:
    url: str
    label: str
    text: str
    title: str = ""


def extract_urls(text: str) -> list[str]:
    seen: set[str] = set()
    urls: list[str] = []
    for match in URL_PATTERN.finditer(text):
        url = match.group(0).rstrip(".,;:")
        if url not in seen:
            seen.add(url)
            urls.append(url)
    return urls


def extract_arxiv_ids(text: str) -> list[str]:
    seen: set[str] = set()
    ids: list[str] = []
    for match in ARXIV_ID_PATTERN.finditer(text):
        arxiv_id = match.group(1)
        if arxiv_id not in seen:
            seen.add(arxiv_id)
            ids.append(arxiv_id)
    return ids


def _arxiv_pdf_url(arxiv_id: str) -> str:
    return f"https://arxiv.org/pdf/{arxiv_id}.pdf"


_ARXIV_METADATA_TIMEOUT = 15.0  # short — best-effort only, PDF fetch must not depend on it


async def _fetch_arxiv_metadata(arxiv_id: str) -> dict[str, str]:
    api_url = _ARXIV_API.format(arxiv_id=arxiv_id)
    try:
        response = await fetch_public_url(
            api_url,
            timeout_seconds=_ARXIV_METADATA_TIMEOUT,
            max_bytes=SOURCE_HTTP_MAX_BYTES,
        )
        root = ET.fromstring(response.text)
        ns = {"atom": "http://www.w3.org/2005/Atom"}
        entry = root.find("atom:entry", ns)
        if entry is None:
            return {"title": "", "summary": ""}
        title = (entry.findtext("atom:title", default="", namespaces=ns) or "").strip()
        summary = (entry.findtext("atom:summary", default="", namespaces=ns) or "").strip()
        return {"title": title, "summary": summary}
    except Exception as exc:
        log.warning("arXiv metadata fetch failed for %s (non-fatal): %s", arxiv_id, exc)
        return {"title": "", "summary": ""}


def extract_html_text(html: str) -> str:
    """Extract readable text from HTML.

    BeautifulSoup gives the best result when installed; the regex fallback keeps
    fetch_url usable in leaner deployments and tests.
    """
    try:
        from bs4 import BeautifulSoup

        soup = BeautifulSoup(html, "html.parser")
        for tag in soup(["script", "style", "noscript", "header", "footer", "nav"]):
            tag.decompose()
        text = soup.get_text(separator="\n")
    except ModuleNotFoundError:
        text = re.sub(
            r"<(script|style|noscript|header|footer|nav)\b[^>]*>.*?</\1>",
            " ",
            html,
            flags=re.IGNORECASE | re.DOTALL,
        )
        text = re.sub(r"<br\s*/?>|</p>|</div>|</li>|</h[1-6]>", "\n", text, flags=re.IGNORECASE)
        text = re.sub(r"<[^>]+>", " ", text)
        text = unescape(text)
    return "\n".join(line.strip() for line in text.splitlines() if line.strip())


def _bounded_max_chars(max_chars: int) -> int:
    return max(0, min(int(max_chars), DEFAULT_MAX_CHARS))


def _extract_pdf_text(content: bytes, max_chars: int) -> str:
    from pypdf import PdfReader

    limit = _bounded_max_chars(max_chars)
    reader = PdfReader(io.BytesIO(content))
    parts: list[str] = []
    consumed = 0
    for page in reader.pages:
        if consumed >= limit:
            break
        text = page.extract_text() or ""
        remaining = limit - consumed
        parts.append(text[:remaining])
        consumed += len(parts[-1])
    return "\n".join(parts).strip()


async def _fetch_pdf_text(url: str, *, max_chars: int = DEFAULT_MAX_CHARS) -> str:
    response = await fetch_public_url(
        url,
        timeout_seconds=DEFAULT_TIMEOUT_SECONDS,
        max_bytes=SOURCE_HTTP_MAX_BYTES,
    )
    return _extract_pdf_text(response.content, max_chars)


def _clip_text(text: str, max_chars: int) -> str:
    max_chars = _bounded_max_chars(max_chars)
    if len(text) <= max_chars:
        return text
    marker = "\n\n[... truncated ...]"
    if max_chars <= len(marker):
        return marker[:max_chars]
    return text[: max_chars - len(marker)] + marker


async def fetch_source_for_arxiv_id(
    arxiv_id: str,
    *,
    max_chars: int = DEFAULT_MAX_CHARS,
) -> FetchedSource | None:
    max_chars = _bounded_max_chars(max_chars)
    pdf_url = _arxiv_pdf_url(arxiv_id)
    label = f"arXiv:{arxiv_id}"
    try:
        metadata = await _fetch_arxiv_metadata(arxiv_id)
        text = await _fetch_pdf_text(pdf_url, max_chars=max_chars)
        if not text and metadata.get("summary"):
            text = metadata["summary"]
        if not text:
            return None
        title = metadata.get("title", "")
        if title:
            label = f"{title} ({label})"
        return FetchedSource(
            url=pdf_url,
            label=label,
            text=_clip_text(text, max_chars),
            title=title,
        )
    except (UnsafeFetchURL, OSError, ValueError, ET.ParseError) as exc:
        log.warning("Failed to fetch arXiv %s: %s", arxiv_id, exc)
        return None


async def fetch_source_for_url(
    url: str,
    *,
    max_chars: int = DEFAULT_MAX_CHARS,
) -> FetchedSource | None:
    max_chars = _bounded_max_chars(max_chars)
    arxiv_ids = extract_arxiv_ids(url)
    if arxiv_ids:
        return await fetch_source_for_arxiv_id(arxiv_ids[0], max_chars=max_chars)

    try:
        response = await fetch_public_url(
            url,
            timeout_seconds=DEFAULT_TIMEOUT_SECONDS,
            max_bytes=SOURCE_HTTP_MAX_BYTES,
        )
        content_type = response.headers.get("content-type", "").lower()
        if "pdf" in content_type or url.lower().endswith(".pdf"):
            text = _extract_pdf_text(response.content, max_chars)
        else:
            text = extract_html_text(response.text)
        if not text:
            return None
        return FetchedSource(
            url=response.url,
            label=url,
            text=_clip_text(text, max_chars),
        )
    except (UnsafeFetchURL, OSError, ValueError) as exc:
        log.warning("Failed to fetch URL %s: %s", url, exc)
        return None


async def fetch_sources_from_prompt(
    prompt: str,
    *,
    max_chars: int = DEFAULT_MAX_CHARS,
) -> list[FetchedSource]:
    """Fetch referenced arXiv papers and URLs from a user prompt."""
    remaining = _bounded_max_chars(max_chars)
    sources: list[FetchedSource] = []
    seen_keys: set[str] = set()

    for arxiv_id in extract_arxiv_ids(prompt):
        if remaining <= 0:
            break
        if arxiv_id in seen_keys:
            continue
        seen_keys.add(arxiv_id)
        source = await fetch_source_for_arxiv_id(arxiv_id, max_chars=remaining)
        if source is not None:
            sources.append(source)
            remaining -= len(source.text)

    for url in extract_urls(prompt):
        if remaining <= 0:
            break
        if extract_arxiv_ids(url):
            continue
        if url in seen_keys:
            continue
        seen_keys.add(url)
        source = await fetch_source_for_url(url, max_chars=remaining)
        if source is not None:
            sources.append(source)
            remaining -= len(source.text)

    return sources


def combine_source_text(
    sources: list[FetchedSource],
    *,
    max_chars: int = DEFAULT_MAX_CHARS,
) -> tuple[str, str]:
    if not sources:
        return "", ""
    labels = [source.label for source in sources]
    label = labels[0] if len(labels) == 1 else "; ".join(labels)
    parts = [f"--- {source.label} ---\n{source.text}" for source in sources]
    return label, _clip_text("\n\n".join(parts), max_chars)


async def build_user_prompt_with_sources(
    prompt: str,
    *,
    max_chars: int = DEFAULT_MAX_CHARS,
) -> tuple[str, list[FetchedSource], bool]:
    """Fetch referenced sources and prepend extracted text to the user message.

    Returns (user_message, sources, fetch_failed_for_referenced_source).
    """
    referenced_ids = extract_arxiv_ids(prompt)
    referenced_urls = extract_urls(prompt)
    sources_referenced = bool(referenced_ids or referenced_urls)

    sources = await fetch_sources_from_prompt(prompt, max_chars=max_chars)
    if sources:
        label, source_text = combine_source_text(sources, max_chars=max_chars)
        user_content = f"{prompt}\n\nFetched source material ({label}):\n{source_text}"
        return user_content, sources, False

    if sources_referenced:
        refs = ", ".join(referenced_ids) or ", ".join(referenced_urls)
        note = (
            f"\n\nNOTE: The system attempted to fetch the referenced source(s) "
            f"({refs}) but failed. Do not invent paper contents from memory; "
            f"state clearly what you cannot verify from the source."
        )
        return prompt + note, [], True

    return prompt, [], False
