"""Upload validation and text extraction (no database, no model)."""

import pytest

from ai_engine.ingestion.extract import DocumentType, ExtractionError, detect_type, extract
from ai_engine.ingestion.normalize import normalize_text
from ai_engine.ingestion.ocr import OcrLine, OcrOptions
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


def test_scanned_pdf_without_ocr_explains_it_is_off() -> None:
    pdf = make_pdf([[], []])

    with pytest.raises(ExtractionError, match="need OCR, which is turned off"):
        extract(DocumentType.PDF, pdf, **LIMITS)


class FakeOcr:
    """Returns the given lines for every page it is asked to read."""

    name = "fake"

    def __init__(self, lines: list[OcrLine]) -> None:
        self.lines = lines
        self.calls = 0

    def read(self, image: object) -> list[OcrLine]:
        self.calls += 1
        return self.lines


def test_only_pages_without_text_are_read_with_ocr() -> None:
    pdf = make_pdf([["This page has a real text layer."], []])
    engine = FakeOcr(
        [
            OcrLine("Scanned warranty terms.", 0.98),
            OcrLine("Claims within 14 days.", 0.95),
            OcrLine("noise", 0.2),
        ]
    )

    document = extract(DocumentType.PDF, pdf, **LIMITS, ocr=OcrOptions(engine))

    assert engine.calls == 1
    assert [(b.page, b.text) for b in document.blocks] == [
        (1, "This page has a real text layer."),
        (2, "Scanned warranty terms.\nClaims within 14 days."),  # the noise line is dropped
    ]
    assert document.ocr_pages == 1
    assert document.warnings == [
        "1 scanned page was read with OCR; recognized text may contain errors."
    ]


def test_mostly_unreadable_pages_keep_only_clear_lines_and_are_reported() -> None:
    # Handwriting: a printed letterhead read well, the rest low-confidence noise.
    engine = FakeOcr(
        [
            OcrLine("H & H BUILDERS PVT. LTD.", 0.99),
            OcrLine("Website: www.example.com", 0.99),
            OcrLine("6cC|93", 0.69),
            OcrLine("0-S-Q-0", 0.78),
            OcrLine("g-nia. 359", 0.56),
            OcrLine("2", 0.94),  # confident, but a lone character
        ]
    )

    document = extract(DocumentType.PDF, make_pdf([[], []]), **LIMITS, ocr=OcrOptions(engine))

    assert [b.text for b in document.blocks] == [
        "H & H BUILDERS PVT. LTD.\nWebsite: www.example.com",
    ] * 2
    assert document.warnings[-1] == (
        "Most of the text on pages 1, 2 couldn't be read (handwriting or a poor scan); "
        "only clearly recognized lines were kept."
    )


def test_ocr_page_limit_is_reported() -> None:
    pdf = make_pdf([[], [], []])
    engine = FakeOcr([OcrLine("text", 0.9)])

    document = extract(DocumentType.PDF, pdf, **LIMITS, ocr=OcrOptions(engine, max_pages=2))

    assert engine.calls == 2
    assert [b.page for b in document.blocks] == [1, 2]
    assert "1 more scanned pages weren't read" in document.warnings[-1]


def test_nothing_readable_even_with_ocr() -> None:
    with pytest.raises(ExtractionError, match="even with OCR"):
        extract(DocumentType.PDF, make_pdf([[]]), **LIMITS, ocr=OcrOptions(FakeOcr([])))


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
