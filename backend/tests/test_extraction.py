"""Upload validation and text extraction (no database, no model)."""

import pytest

from ai_engine.ingestion.extract import DocumentType, ExtractionError, detect_type, extract
from ai_engine.ingestion.normalize import normalize_text
from tests.samples import make_docx, make_pdf, make_zip_bomb_docx

LIMITS = {"max_pages": 50, "max_characters": 100_000}


@pytest.mark.parametrize(
    ("filename", "content", "message"),
    [
        ("virus.exe", b"MZ...", "Unsupported file type"),
        ("report.pdf", b"PK\x03\x04 not a pdf", "doesn't look like a PDF"),
        ("report.docx", b"%PDF-1.4 not a docx", "doesn't look like a Word"),
        ("notes.txt", "café".encode("latin-1"), "must be UTF-8"),
        ("notes.md", b"text\x00with a NUL", "looks binary"),
        ("empty.txt", b"", "empty"),
    ],
    ids=["extension", "fake-pdf", "fake-docx", "not-utf8", "binary", "empty"],
)
def test_type_is_confirmed_from_content(filename: str, content: bytes, message: str) -> None:
    with pytest.raises(ExtractionError, match=message):
        detect_type(filename, content)


def test_docx_zip_bombs_are_refused() -> None:
    with pytest.raises(ExtractionError, match="too large"):
        detect_type("bomb.docx", make_zip_bomb_docx())


def test_pdf_keeps_page_numbers() -> None:
    pdf = make_pdf([["Welcome to the guide.", "Second line."], ["Refunds take five days."]])

    document = extract(detect_type("guide.pdf", pdf), pdf, **LIMITS)

    assert document.page_count == 2
    assert [(block.page, block.text) for block in document.blocks] == [
        (1, "Welcome to the guide.\nSecond line."),
        (2, "Refunds take five days."),
    ]


def test_scanned_pdf_without_text_explains_ocr() -> None:
    pdf = make_pdf([[], []])

    with pytest.raises(ExtractionError, match="OCR"):
        extract(DocumentType.PDF, pdf, **LIMITS)


def test_password_protected_pdf_is_refused() -> None:
    pdf = make_pdf([["secret"]], encrypt_with="hunter2")

    with pytest.raises(ExtractionError, match="password-protected"):
        extract(DocumentType.PDF, pdf, **LIMITS)


def test_permissions_only_encrypted_pdf_is_read() -> None:
    """AES-encrypted with an owner password only (printing/copying restricted)."""
    pdf = make_pdf([["Readable by anyone."]], encrypt_with="", owner_password="admin-only")

    blocks = extract(DocumentType.PDF, pdf, **LIMITS).blocks

    assert [block.text for block in blocks] == ["Readable by anyone."]


def test_page_and_character_limits() -> None:
    pdf = make_pdf([["one"], ["two"], ["three"]])
    with pytest.raises(ExtractionError, match="3 pages; the limit is 2"):
        extract(DocumentType.PDF, pdf, max_pages=2, max_characters=1_000)

    with pytest.raises(ExtractionError, match="processing limit"):
        extract(DocumentType.TXT, b"x" * 200, max_pages=10, max_characters=100)


def test_docx_headings_paragraphs_and_tables() -> None:
    docx = make_docx(
        [("Billing", ["Invoices are sent monthly."]), ("Support", ["Open a ticket."])],
        table=[["Plan", "Price"], ["Pro", "$20"]],
    )

    blocks = extract(detect_type("handbook.docx", docx), docx, **LIMITS).blocks

    assert [(b.text, b.heading, b.is_heading) for b in blocks] == [
        ("Billing", "Billing", True),
        ("Invoices are sent monthly.", "Billing", False),
        ("Support", "Support", True),
        ("Open a ticket.", "Support", False),
        ("Plan | Price\nPro | $20", "Support", False),
    ]


def test_markdown_headings_ignore_code_fences() -> None:
    md = (
        b"# Install\nRun the installer.\n\n"
        b"```bash\n# not a heading\necho hi\n```\n\n"
        b"## Configure\nSet the key."
    )

    blocks = extract(detect_type("README.md", md), md, **LIMITS).blocks

    assert [b.heading for b in blocks if b.is_heading] == ["Install", "Configure"]
    assert any("# not a heading" in b.text and b.heading == "Install" for b in blocks)


def test_normalization() -> None:
    ligature_fi, no_break_space, form_feed = chr(0xFB01), chr(0xA0), chr(0x0C)
    windows_blank_lines = "\r\n" * 4
    raw = (
        f"{ligature_fi}le{no_break_space}name{windows_blank_lines}"
        f"next{form_feed}page  with   spaces"
    )

    assert normalize_text(raw) == "file name\n\nnext page with spaces"
