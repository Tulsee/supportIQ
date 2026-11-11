"""The real OCR engine (RapidOCR; its models ship with the package) on a scan."""

import pytest

from ai_engine.ingestion.extract import DocumentType, detect_type, extract
from ai_engine.ingestion.ocr import OcrOptions, RapidOcrEngine
from tests.samples import make_scanned_pdf


@pytest.fixture(scope="module")
def engine() -> RapidOcrEngine:
    return RapidOcrEngine()


def test_scanned_pdf_is_read_with_ocr(engine: RapidOcrEngine) -> None:
    pdf = make_scanned_pdf(
        [
            ["Warranty claims must be filed within 14 days."],
            ["Error code E-4012 means the fan has stopped.", "Replace the fan module."],
        ]
    )
    kind = detect_type("scan.pdf", pdf)

    document = extract(kind, pdf, max_pages=10, max_characters=100_000, ocr=OcrOptions(engine))

    assert kind is DocumentType.PDF
    assert document.ocr_pages == 2
    by_page = {block.page: block.text for block in document.blocks}
    assert "within 14 days" in by_page[1]
    assert "E-4012" in by_page[2] and "fan module" in by_page[2]
