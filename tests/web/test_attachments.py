import base64
import struct
import zlib

import pytest

from math_agent.web import attachments as att
from math_agent.web.attachments import (
    ATTACHMENT_ONLY_PROBLEM,
    pdf_data_url_to_png_data_urls,
    resolve_problem_text,
    to_image_parts,
)


def _solid_png_data_url(w=8, h=8, rgb=(10, 20, 30)) -> str:
    raw = b"".join(b"\x00" + bytes(rgb) * w for _ in range(h))

    def chunk(t, d):
        return struct.pack(">I", len(d)) + t + d + struct.pack(">I", zlib.crc32(t + d) & 0xffffffff)

    png = b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0)) \
        + chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b"")
    return "data:image/png;base64," + base64.b64encode(png).decode()


def _data_url(media_type: str, payload: bytes) -> str:
    return f"data:{media_type};base64," + base64.b64encode(payload).decode()


def test_resolve_problem_text_prefers_user_text():
    assert resolve_problem_text("  prove x  ", [{"type": "image_url"}]) == "prove x"


def test_resolve_problem_text_uses_attachment_fallback():
    assert resolve_problem_text("   ", [{"type": "image_url"}]) == ATTACHMENT_ONLY_PROBLEM
    assert resolve_problem_text("", [{"type": "image_url"}]) == ATTACHMENT_ONLY_PROBLEM


def test_resolve_problem_text_rejects_empty_without_attachments():
    assert resolve_problem_text("", None) is None
    assert resolve_problem_text("  ", []) is None


def test_image_passes_through_as_one_part():
    parts, notices = to_image_parts([{"kind": "image", "data_url": _solid_png_data_url()}])
    assert len(parts) == 1
    assert parts[0]["type"] == "image_url"
    assert parts[0]["image_url"]["url"].startswith("data:image/png")
    assert notices == []


@pytest.mark.parametrize(
    "media_type,payload",
    [
        ("image/jpeg", b"\xff\xd8\xff\xe0jpeg"),
        ("image/webp", b"RIFF\x04\x00\x00\x00WEBPdata"),
        ("image/gif", b"GIF89aimage"),
    ],
)
def test_supported_image_signatures_are_preserved(media_type, payload):
    data_url = _data_url(media_type, payload)

    parts, notices = to_image_parts([{"kind": "image", "data_url": data_url}])

    assert parts == [{"type": "image_url", "image_url": {"url": data_url}}]
    assert notices == []


def test_unknown_kind_is_ignored_with_notice():
    parts, notices = to_image_parts([{"kind": "audio", "data_url": "data:audio/mp3;base64,AAAA"}])
    assert parts == []
    assert any("audio" in n.lower() for n in notices)


def test_non_dict_entry_is_skipped_without_raising():
    parts, notices = to_image_parts(["not-a-dict", 42, None, {"kind": "image", "data_url": _solid_png_data_url()}])
    assert len(parts) == 1
    assert parts[0]["type"] == "image_url"
    assert any("invalid attachment" in n.lower() for n in notices)


def test_pdf_truncation_notice(monkeypatch):
    # Stub rasterization so the test does not require poppler.
    monkeypatch.setattr(
        att,
        "pdf_bytes_to_png_data_urls",
        lambda pdf_bytes, max_pages, **kwargs: (
            ["data:image/png;base64,AAAA"] * max_pages,
            9,
            max_pages * 3,
        ),
    )
    parts, notices = to_image_parts(
        [{"kind": "pdf", "data_url": _data_url("application/pdf", b"%PDF-1.7\n")}],
        max_pdf_pages=5,
    )
    assert len(parts) == 5
    assert any("5 of 9" in n for n in notices)


@pytest.mark.parametrize(
    "kind,data_url",
    [
        ("image", "not-a-data-url"),
        ("image", "data:image/png,AAAA"),
        ("image", "data:image/png;base64,not***base64"),
        ("pdf", "data:application/pdf;base64,%%%="),
    ],
)
def test_malformed_or_non_base64_data_urls_are_skipped(kind, data_url):
    parts, notices = to_image_parts([{"kind": kind, "data_url": data_url}])

    assert parts == []
    assert any("invalid" in notice.lower() for notice in notices)


def test_attachment_count_is_bounded_before_processing():
    files = [
        {"kind": "image", "data_url": _solid_png_data_url(rgb=(index, 20, 30))}
        for index in range(4)
    ]

    parts, notices = to_image_parts(files, max_files=2)

    assert len(parts) == 2
    assert any("2" in notice and "file" in notice.lower() for notice in notices)


def test_aggregate_decoded_bytes_are_bounded_before_large_decode(monkeypatch):
    real_b64decode = base64.b64decode
    decoded_payload_lengths: list[int] = []

    def tracking_b64decode(payload, *args, **kwargs):
        decoded_payload_lengths.append(len(payload))
        return real_b64decode(payload, *args, **kwargs)

    monkeypatch.setattr(att.base64, "b64decode", tracking_b64decode)
    small = _solid_png_data_url()
    small_payload = small.partition(",")[2]
    small_decoded_bytes = len(real_b64decode(small_payload))
    over_remaining_budget = "data:image/png;base64," + ("A" * 400)

    parts, notices = to_image_parts(
        [
            {"kind": "image", "data_url": small},
            {"kind": "image", "data_url": over_remaining_budget},
        ],
        max_total_decoded_bytes=small_decoded_bytes,
    )

    assert len(parts) == 1
    assert decoded_payload_lengths == [len(small_payload)]
    assert any("byte" in notice.lower() and "limit" in notice.lower() for notice in notices)


def test_pdf_conversion_requests_only_bounded_pages_and_reports_total(monkeypatch):
    import pdf2image

    calls: list[dict] = []
    pages = []

    class FakePage:
        def __init__(self):
            self.close_calls = 0

        def save(self, buffer, format):
            assert format == "PNG"
            buffer.write(b"png")

        def close(self):
            self.close_calls += 1

    def fake_convert(pdf_bytes, **kwargs):
        calls.append(kwargs)
        pages.extend([FakePage(), FakePage(), FakePage()])
        return pages

    monkeypatch.setattr(att, "_pdf_page_count", lambda pdf_bytes: 9)
    monkeypatch.setattr(pdf2image, "convert_from_bytes", fake_convert)

    urls, total = pdf_data_url_to_png_data_urls(
        _data_url("application/pdf", b"%PDF-1.7\n"),
        max_pages=3,
    )

    assert len(urls) == 3
    assert total == 9
    assert calls == [{
        "first_page": 1,
        "last_page": 3,
        "thread_count": 1,
        "size": att.MAX_PDF_RASTER_DIMENSION,
        "timeout": att.PDF_CONVERSION_TIMEOUT_SECONDS,
    }]
    assert all(page.close_calls == 1 for page in pages)


def test_pdf_is_decoded_once_before_conversion(monkeypatch):
    real_b64decode = base64.b64decode
    decode_calls = 0

    def tracking_decode(payload, *args, **kwargs):
        nonlocal decode_calls
        decode_calls += 1
        return real_b64decode(payload, *args, **kwargs)

    converted: list[bytes] = []

    def fake_convert(pdf_bytes, max_pages, **kwargs):
        converted.append(pdf_bytes)
        return ["data:image/png;base64,cG5n"], 1, 3

    monkeypatch.setattr(att.base64, "b64decode", tracking_decode)
    monkeypatch.setattr(att, "pdf_bytes_to_png_data_urls", fake_convert)
    payload = b"%PDF-1.7\ncontent"

    parts, notices = to_image_parts([
        {"kind": "pdf", "data_url": _data_url("application/pdf", payload)}
    ])

    assert decode_calls == 1
    assert converted == [payload]
    assert len(parts) == 1
    assert notices == []


def test_pdf_rendered_output_cap_closes_every_page(monkeypatch):
    import pdf2image

    class FakePage:
        def __init__(self, payload: bytes):
            self.payload = payload
            self.close_calls = 0

        def save(self, buffer, format):
            buffer.write(self.payload)

        def close(self):
            self.close_calls += 1

    pages = [FakePage(b"1234"), FakePage(b"5678")]
    monkeypatch.setattr(att, "_pdf_page_count", lambda pdf_bytes: 2)
    monkeypatch.setattr(pdf2image, "convert_from_bytes", lambda *args, **kwargs: pages)

    with pytest.raises(att._AttachmentRenderedLimitExceeded):
        att.pdf_bytes_to_png_data_urls(
            b"%PDF-1.7\n",
            max_pages=2,
            max_rendered_bytes=7,
        )

    assert [page.close_calls for page in pages] == [1, 1]


def test_pdf_pages_are_closed_when_png_encoding_fails(monkeypatch):
    import pdf2image

    class FakePage:
        def __init__(self, fail=False):
            self.fail = fail
            self.close_calls = 0

        def save(self, buffer, format):
            if self.fail:
                raise OSError("broken page")
            buffer.write(b"png")

        def close(self):
            self.close_calls += 1

    pages = [FakePage(fail=True), FakePage()]
    monkeypatch.setattr(att, "_pdf_page_count", lambda pdf_bytes: 2)
    monkeypatch.setattr(pdf2image, "convert_from_bytes", lambda *args, **kwargs: pages)

    with pytest.raises(OSError, match="broken page"):
        att.pdf_bytes_to_png_data_urls(b"%PDF-1.7\n", max_pages=2)

    assert [page.close_calls for page in pages] == [1, 1]


def test_pdf_conversion_timeout_is_configured(monkeypatch):
    import pdf2image
    from pdf2image.exceptions import PDFPopplerTimeoutError

    captured: dict = {}

    def timeout(pdf_bytes, **kwargs):
        captured.update(kwargs)
        raise PDFPopplerTimeoutError("timed out")

    monkeypatch.setattr(att, "_pdf_page_count", lambda pdf_bytes: 1)
    monkeypatch.setattr(pdf2image, "convert_from_bytes", timeout)

    with pytest.raises(PDFPopplerTimeoutError):
        att.pdf_bytes_to_png_data_urls(b"%PDF-1.7\n", max_pages=1)

    assert captured["timeout"] == att.PDF_CONVERSION_TIMEOUT_SECONDS
    assert captured["thread_count"] == 1
    assert captured["size"] == att.MAX_PDF_RASTER_DIMENSION


def test_rendered_pdf_budget_is_aggregate_across_files(monkeypatch):
    remaining_budgets: list[int] = []

    def fake_convert(pdf_bytes, max_pages, *, max_rendered_bytes, **kwargs):
        remaining_budgets.append(max_rendered_bytes)
        if len(remaining_budgets) == 1:
            return ["data:image/png;base64,cG5n"], 1, 3
        raise att._AttachmentRenderedLimitExceeded("too large")

    monkeypatch.setattr(att, "pdf_bytes_to_png_data_urls", fake_convert)
    pdf = _data_url("application/pdf", b"%PDF-1.7\n")

    parts, notices = to_image_parts(
        [{"kind": "pdf", "data_url": pdf}, {"kind": "pdf", "data_url": pdf}],
        max_total_rendered_bytes=5,
    )

    assert len(parts) == 1
    assert remaining_budgets == [5, 2]
    assert any("rendered" in notice.lower() and "limit" in notice.lower() for notice in notices)


@pytest.mark.parametrize(
    "media_type,payload",
    [
        ("image/png", b"not-png"),
        ("image/jpeg", b"not-jpeg"),
        ("image/webp", b"not-webp"),
        ("image/gif", b"not-gif"),
        ("image/svg+xml", b"<svg></svg>"),
    ],
)
def test_fake_or_unsupported_image_payloads_are_rejected(media_type, payload):
    parts, notices = to_image_parts([
        {"kind": "image", "data_url": _data_url(media_type, payload)}
    ])

    assert parts == []
    assert any("invalid image" in notice.lower() for notice in notices)


def test_default_decoded_attachment_budget_is_eleven_mebibytes():
    assert att.MAX_ATTACHMENT_DECODED_BYTES == 11 * 1024 * 1024
    assert att.MAX_SOLVE_REQUEST_BYTES == 16 * 1024 * 1024
