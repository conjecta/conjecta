from __future__ import annotations

import base64
import binascii
import io
import logging
from typing import Any

log = logging.getLogger("math_agent.web.attachments")

ATTACHMENT_ONLY_PROBLEM = "请根据附件中的题目进行求解。"
MAX_ATTACHMENT_FILES = 8
MAX_ATTACHMENT_DECODED_BYTES = 11 * 1024 * 1024
MAX_SOLVE_REQUEST_BYTES = 16 * 1024 * 1024
MAX_PDF_PAGES = 5
MAX_PDF_RASTER_DIMENSION = 2048
MAX_PDF_RENDERED_BYTES = 12 * 1024 * 1024
PDF_CONVERSION_TIMEOUT_SECONDS = 30
_MAX_DATA_URL_HEADER_CHARS = 256
_SUPPORTED_IMAGE_MEDIA_TYPES = frozenset(
    {"image/png", "image/jpeg", "image/webp", "image/gif"}
)


class _InvalidDataURL(ValueError):
    pass


class _AttachmentByteLimitExceeded(ValueError):
    pass


class _AttachmentRenderedLimitExceeded(ValueError):
    pass


def resolve_problem_text(problem: str, attachments: list[dict] | None) -> str | None:
    """Return the problem text to solve, or None if neither text nor attachments exist."""
    text = (problem or "").strip()
    if text:
        return text
    if attachments:
        return ATTACHMENT_ONLY_PROBLEM
    return None


def _image_part(data_url: str) -> dict:
    return {"type": "image_url", "image_url": {"url": data_url}}


def _bounded_int(value: Any, *, default: int, maximum: int, minimum: int = 0) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError, OverflowError):
        parsed = default
    return max(minimum, min(parsed, maximum))


def _has_valid_image_signature(media_type: str, payload: bytes) -> bool:
    if media_type == "image/png":
        return payload.startswith(b"\x89PNG\r\n\x1a\n")
    if media_type == "image/jpeg":
        return payload.startswith(b"\xff\xd8\xff")
    if media_type == "image/webp":
        return (
            len(payload) >= 12
            and payload.startswith(b"RIFF")
            and payload[8:12] == b"WEBP"
        )
    if media_type == "image/gif":
        return payload.startswith((b"GIF87a", b"GIF89a"))
    return False


def _has_valid_pdf_signature(payload: bytes) -> bool:
    return b"%PDF-" in payload[:1024]


def _decode_data_url(
    data_url: Any,
    *,
    kind: str,
    max_decoded_bytes: int,
) -> bytes:
    """Strictly decode one bounded base64 data URL.

    The encoded payload is not sliced or decoded until its decoded-size upper
    bound fits the remaining request budget. This keeps rejected payloads from
    causing a second, attacker-sized allocation.
    """
    if not isinstance(data_url, str):
        raise _InvalidDataURL("data URL must be a string")

    comma = data_url.find(",")
    if comma <= len("data:") or comma > _MAX_DATA_URL_HEADER_CHARS:
        raise _InvalidDataURL("malformed data URL header")

    header = data_url[:comma]
    if not header.lower().startswith("data:"):
        raise _InvalidDataURL("missing data URL scheme")
    metadata = header[len("data:"):].split(";")
    if len(metadata) < 2 or metadata[-1].lower() != "base64":
        raise _InvalidDataURL("attachment data URL must be base64 encoded")

    media_type = metadata[0].lower()
    if kind == "image":
        if media_type not in _SUPPORTED_IMAGE_MEDIA_TYPES:
            raise _InvalidDataURL("image attachment has an invalid media type")
    elif kind == "pdf":
        if media_type != "application/pdf":
            raise _InvalidDataURL("PDF attachment has an invalid media type")
    else:  # pragma: no cover - callers validate kinds first
        raise _InvalidDataURL("unsupported attachment kind")

    payload_length = len(data_url) - comma - 1
    if payload_length <= 0 or payload_length % 4:
        raise _InvalidDataURL("invalid base64 payload length")
    padding = 2 if data_url.endswith("==") else 1 if data_url.endswith("=") else 0
    decoded_size = (payload_length // 4) * 3 - padding
    if decoded_size > max_decoded_bytes:
        raise _AttachmentByteLimitExceeded("decoded attachment exceeds byte budget")

    # This slice is now bounded by max_decoded_bytes (plus base64 overhead).
    payload = data_url[comma + 1:]
    try:
        decoded = base64.b64decode(payload, validate=True)
    except (ValueError, TypeError, binascii.Error) as exc:
        raise _InvalidDataURL("invalid base64 payload") from exc
    if len(decoded) != decoded_size:
        raise _InvalidDataURL("invalid base64 payload size")
    if kind == "image" and not _has_valid_image_signature(media_type, decoded):
        raise _InvalidDataURL("image attachment signature does not match its media type")
    if kind == "pdf" and not _has_valid_pdf_signature(decoded):
        raise _InvalidDataURL("PDF attachment signature is invalid")
    return decoded


def _pdf_page_count(pdf_bytes: bytes) -> int:
    """Read a PDF page count without starting an additional Poppler process."""
    from pypdf import PdfReader

    total = len(PdfReader(io.BytesIO(pdf_bytes), strict=False).pages)
    if total < 0:  # pragma: no cover - defensive against a broken parser
        raise ValueError("PDF page count is invalid")
    return total


def pdf_bytes_to_png_data_urls(
    pdf_bytes: bytes,
    max_pages: int,
    *,
    max_rendered_bytes: int = MAX_PDF_RENDERED_BYTES,
) -> tuple[list[str], int, int]:
    """Rasterize bounded PDF bytes once and return URLs, total pages, and PNG bytes."""
    from pdf2image import convert_from_bytes

    page_limit = _bounded_int(
        max_pages,
        default=MAX_PDF_PAGES,
        maximum=MAX_PDF_PAGES,
        minimum=1,
    )
    rendered_limit = _bounded_int(
        max_rendered_bytes,
        default=MAX_PDF_RENDERED_BYTES,
        maximum=MAX_PDF_RENDERED_BYTES,
    )
    total = _pdf_page_count(pdf_bytes)
    if total == 0:
        return [], 0, 0

    images = convert_from_bytes(
        pdf_bytes,
        first_page=1,
        last_page=min(page_limit, total),
        thread_count=1,
        size=MAX_PDF_RASTER_DIMENSION,
        timeout=PDF_CONVERSION_TIMEOUT_SECONDS,
    )
    urls: list[str] = []
    rendered_bytes = 0
    closed_pages: set[int] = set()

    def close_page(page: Any) -> None:
        page_id = id(page)
        if page_id in closed_pages:
            return
        closed_pages.add(page_id)
        close = getattr(page, "close", None)
        if callable(close):
            try:
                close()
            except Exception as exc:  # resource cleanup must continue for other pages
                log.debug("Failed to close rendered PDF page: %s", exc)

    try:
        for page in images[:page_limit]:
            try:
                with io.BytesIO() as buf:
                    page.save(buf, format="PNG")
                    png_bytes = buf.getvalue()
                if len(png_bytes) > rendered_limit - rendered_bytes:
                    raise _AttachmentRenderedLimitExceeded(
                        "rendered PDF pages exceed output byte budget"
                    )
                rendered_bytes += len(png_bytes)
                urls.append(
                    "data:image/png;base64,"
                    + base64.b64encode(png_bytes).decode()
                )
            finally:
                close_page(page)
    finally:
        for page in images:
            close_page(page)
    return urls, total, rendered_bytes


def pdf_data_url_to_png_data_urls(data_url: str, max_pages: int) -> tuple[list[str], int]:
    """Compatibility wrapper for callers with a PDF data URL."""
    pdf_bytes = _decode_data_url(
        data_url,
        kind="pdf",
        max_decoded_bytes=MAX_ATTACHMENT_DECODED_BYTES,
    )
    urls, total, _ = pdf_bytes_to_png_data_urls(pdf_bytes, max_pages)
    return urls, total


def to_image_parts(
    files: list[dict] | None,
    max_pdf_pages: int = MAX_PDF_PAGES,
    *,
    max_files: int = MAX_ATTACHMENT_FILES,
    max_total_decoded_bytes: int = MAX_ATTACHMENT_DECODED_BYTES,
    max_total_rendered_bytes: int = MAX_PDF_RENDERED_BYTES,
) -> tuple[list[dict], list[str]]:
    """Validate and convert a bounded collection of image/PDF attachments."""
    parts: list[dict] = []
    notices: list[str] = []
    if files is None:
        return parts, notices
    if not isinstance(files, list):
        return parts, ["Skipped an invalid attachment list."]

    file_limit = _bounded_int(
        max_files,
        default=MAX_ATTACHMENT_FILES,
        maximum=MAX_ATTACHMENT_FILES,
    )
    byte_limit = _bounded_int(
        max_total_decoded_bytes,
        default=MAX_ATTACHMENT_DECODED_BYTES,
        maximum=MAX_ATTACHMENT_DECODED_BYTES,
    )
    page_limit = _bounded_int(
        max_pdf_pages,
        default=MAX_PDF_PAGES,
        maximum=MAX_PDF_PAGES,
        minimum=1,
    )
    rendered_limit = _bounded_int(
        max_total_rendered_bytes,
        default=MAX_PDF_RENDERED_BYTES,
        maximum=MAX_PDF_RENDERED_BYTES,
    )
    if len(files) > file_limit:
        notices.append(
            f"Attachment limit is {file_limit} file(s); "
            f"skipped {len(files) - file_limit} extra attachment(s)."
        )

    total_decoded_bytes = 0
    total_rendered_bytes = 0
    for f in files[:file_limit]:
        if not isinstance(f, dict):
            notices.append("Skipped an invalid attachment entry.")
            continue
        raw_kind = f.get("kind")
        kind = raw_kind.strip().lower() if isinstance(raw_kind, str) else ""
        if kind not in {"image", "pdf"}:
            notices.append(f"Unsupported attachment kind: {kind or 'unknown'}.")
            continue

        data_url = f.get("data_url")
        try:
            decoded = _decode_data_url(
                data_url,
                kind=kind,
                max_decoded_bytes=byte_limit - total_decoded_bytes,
            )
        except _AttachmentByteLimitExceeded:
            notices.append(
                "Skipped attachment because the aggregate decoded-byte "
                f"limit of {byte_limit} bytes would be exceeded."
            )
            continue
        except _InvalidDataURL:
            notices.append(f"Skipped an invalid {kind} attachment data URL.")
            continue

        total_decoded_bytes += len(decoded)
        if kind == "image":
            parts.append(_image_part(data_url))
        else:
            try:
                urls, total, rendered_bytes = pdf_bytes_to_png_data_urls(
                    decoded,
                    page_limit,
                    max_rendered_bytes=rendered_limit - total_rendered_bytes,
                )
            except _AttachmentRenderedLimitExceeded:
                notices.append(
                    "Skipped PDF because the aggregate rendered-byte "
                    f"limit of {rendered_limit} bytes would be exceeded."
                )
                continue
            except Exception as exc:  # poppler missing / corrupt PDF
                log.warning("PDF rasterization failed: %s", exc)
                notices.append("Could not read the PDF (server missing PDF support).")
                continue
            total_rendered_bytes += rendered_bytes
            parts.extend(_image_part(u) for u in urls)
            if total > page_limit:
                notices.append(f"PDF truncated to {page_limit} of {total} pages.")
    return parts, notices
