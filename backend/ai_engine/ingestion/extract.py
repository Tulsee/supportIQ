"""Validate uploads and extract their text with provenance.

Supported: PDF (text per page; scanned pages via OCR), DOCX (paragraphs, tables,
heading styles), plain text, and Markdown (``#`` headings). The file type is
decided by the extension *and* confirmed from the content, so a renamed executable
is rejected.

Every limit that bounds work on untrusted input lives here: page count, extracted
characters, and decompressed size for DOCX (a ZIP archive).
"""

import io
import re
import zipfile
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import PurePath

from ai_engine.ingestion.normalize import normalize_text
from ai_engine.ingestion.ocr import OcrOptions, render_page


class DocumentType(StrEnum):
    PDF = "pdf"
    DOCX = "docx"
    TXT = "txt"
    MARKDOWN = "markdown"


EXTENSIONS: dict[str, DocumentType] = {
    ".pdf": DocumentType.PDF,
    ".docx": DocumentType.DOCX,
    ".txt": DocumentType.TXT,
    ".md": DocumentType.MARKDOWN,
    ".markdown": DocumentType.MARKDOWN,
}

MEDIA_TYPES: dict[DocumentType, str] = {
    DocumentType.PDF: "application/pdf",
    DocumentType.DOCX: "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    DocumentType.TXT: "text/plain; charset=utf-8",
    DocumentType.MARKDOWN: "text/markdown; charset=utf-8",
}

# DOCX is a ZIP archive: refuse archives that would inflate beyond these sizes.
MAX_DOCX_UNCOMPRESSED_BYTES = 200 * 1024 * 1024
MAX_DOCX_COMPRESSION_RATIO = 200


class ExtractionError(Exception):
    """The file can't be processed. The message is safe to show to users."""


@dataclass(frozen=True)
class TextBlock:
    """A paragraph-like unit of text and where it came from."""

    text: str
    page: int | None = None  # 1-based page number (PDF only)
    heading: str | None = None  # section heading in effect for this block
    is_heading: bool = False  # the block is itself a heading


@dataclass(frozen=True)
class ExtractedDocument:
    blocks: list[TextBlock]
    page_count: int | None = None
    warnings: list[str] = field(default_factory=list)
    ocr_pages: int = 0  # pages whose text came from OCR

    @property
    def character_count(self) -> int:
        return sum(len(block.text) for block in self.blocks)


# --- Validation ------------------------------------------------------------------------


def detect_type(filename: str, content: bytes) -> DocumentType:
    """The document type, if the name and the content agree; else ExtractionError."""
    suffix = PurePath(filename).suffix.lower()
    kind = EXTENSIONS.get(suffix)
    if kind is None:
        raise ExtractionError("Unsupported file type. Upload a PDF, DOCX, TXT, or Markdown file.")
    if not content:
        raise ExtractionError("The file is empty.")
    if kind is DocumentType.PDF and b"%PDF-" not in content[:1024]:
        raise ExtractionError("This file doesn't look like a PDF.")
    if kind is DocumentType.DOCX:
        _check_docx_archive(content)
    if kind in (DocumentType.TXT, DocumentType.MARKDOWN):
        decode_text(content)
    return kind


def _check_docx_archive(content: bytes) -> None:
    if not zipfile.is_zipfile(io.BytesIO(content)):
        raise ExtractionError("This file doesn't look like a Word (.docx) document.")
    with zipfile.ZipFile(io.BytesIO(content)) as archive:
        names = set(archive.namelist())
        if "word/document.xml" not in names or "[Content_Types].xml" not in names:
            raise ExtractionError("This file doesn't look like a Word (.docx) document.")
        total = sum(info.file_size for info in archive.infolist())
        if total > MAX_DOCX_UNCOMPRESSED_BYTES or total > len(content) * MAX_DOCX_COMPRESSION_RATIO:
            raise ExtractionError("This Word document is too large to process.")


def decode_text(content: bytes) -> str:
    try:
        text = content.decode("utf-8-sig")
    except UnicodeDecodeError:
        raise ExtractionError("Text files must be UTF-8 encoded.") from None
    if "\x00" in text:
        raise ExtractionError("This file looks binary, not like text.")
    return text


# --- Extraction --------------------------------------------------------------------------


def extract(
    kind: DocumentType,
    content: bytes,
    *,
    max_pages: int,
    max_characters: int,
    ocr: OcrOptions | None = None,
) -> ExtractedDocument:
    """Extract text blocks. Raises ExtractionError for unreadable or oversized files.
    With ``ocr``, PDF pages without a text layer (scans) are read with OCR."""
    if kind is DocumentType.PDF:
        document = _extract_pdf(content, max_pages=max_pages, ocr=ocr)
    elif kind is DocumentType.DOCX:
        document = _extract_docx(content)
    elif kind is DocumentType.MARKDOWN:
        document = _extract_markdown(decode_text(content))
    else:
        document = ExtractedDocument(blocks=_paragraphs(decode_text(content)))

    if document.character_count > max_characters:
        raise ExtractionError(
            f"This document has more than {max_characters:,} characters of text, "
            "which is above the processing limit."
        )
    if not any(block.text.strip() for block in document.blocks):
        if kind is DocumentType.PDF and ocr is None:
            raise ExtractionError(
                "No text could be extracted. Scanned PDFs (images of pages) need OCR, "
                "which is turned off."
            )
        if kind is DocumentType.PDF:
            raise ExtractionError("No text could be extracted from this PDF, even with OCR.")
        raise ExtractionError("The document contains no text.")
    return document


def _paragraphs(
    text: str, *, page: int | None = None, heading: str | None = None
) -> list[TextBlock]:
    return [
        TextBlock(paragraph, page=page, heading=heading)
        for paragraph in re.split(r"\n\s*\n", normalize_text(text))
        if paragraph.strip()
    ]


def _extract_pdf(content: bytes, *, max_pages: int, ocr: OcrOptions | None) -> ExtractedDocument:
    from pypdf import PdfReader
    from pypdf.errors import DependencyError, PdfReadError

    try:
        reader = PdfReader(io.BytesIO(content))
        # Many PDFs are "encrypted" only to restrict printing or copying, with an
        # empty user password: those open fine. A real password can't be guessed.
        if reader.is_encrypted and not reader.decrypt(""):
            raise ExtractionError("This PDF is password-protected.")
        page_count = len(reader.pages)
    except ExtractionError:
        raise
    except DependencyError as exc:
        raise ExtractionError("This PDF uses an unsupported encryption method.") from exc
    except (PdfReadError, ValueError, KeyError, TypeError, OSError) as exc:
        raise ExtractionError("The PDF couldn't be read. It may be damaged.") from exc
    if page_count > max_pages:
        raise ExtractionError(f"This PDF has {page_count:,} pages; the limit is {max_pages:,}.")

    texts: list[str] = []
    warnings: list[str] = []
    for number, page in enumerate(reader.pages, start=1):
        try:
            raw = page.extract_text() or ""
        except Exception:  # one bad page shouldn't sink the document
            warnings.append(f"Page {number} couldn't be read and was skipped.")
            raw = ""
        # Words hyphenated across a line break: "configu-\nration" -> "configuration".
        texts.append(re.sub(r"(\w)-\n(?=[a-z])", r"\1", raw))

    ocr_pages = 0
    if ocr is not None:
        ocr_pages = _ocr_scanned_pages(content, texts, ocr, warnings)

    blocks = [
        block
        for number, text in enumerate(texts, start=1)
        for block in _paragraphs(text, page=number)
    ]
    return ExtractedDocument(
        blocks=blocks, page_count=page_count, warnings=warnings, ocr_pages=ocr_pages
    )


def _ocr_scanned_pages(
    content: bytes, texts: list[str], ocr: OcrOptions, warnings: list[str]
) -> int:
    """Replace the text of pages with (almost) none by what OCR reads on them.
    Returns how many pages yielded text."""
    import pypdfium2

    scanned = [i for i, text in enumerate(texts) if len(text.strip()) < ocr.min_text_chars]
    if not scanned:
        return 0
    try:
        pdf = pypdfium2.PdfDocument(content)
    except pypdfium2.PdfiumError:
        warnings.append("Scanned pages couldn't be rendered for OCR.")
        return 0
    read = 0
    try:
        for index in scanned[: ocr.max_pages]:
            try:
                image = render_page(pdf, index, dpi=ocr.dpi)
            except pypdfium2.PdfiumError:
                warnings.append(f"Page {index + 1} couldn't be rendered for OCR.")
                continue
            lines = [
                line.text
                for line in ocr.engine.read(image)
                if line.confidence >= ocr.min_confidence and line.text.strip()
            ]
            if lines:
                texts[index] = "\n".join(lines)
                read += 1
    finally:
        pdf.close()
    if read:
        pages = "page was" if read == 1 else "pages were"
        warnings.append(
            f"{read} scanned {pages} read with OCR; recognized text may contain errors."
        )
    if len(scanned) > ocr.max_pages:
        warnings.append(
            f"{len(scanned) - ocr.max_pages} more scanned pages weren't read "
            f"(the OCR limit is {ocr.max_pages} pages per document)."
        )
    return read


def _extract_docx(content: bytes) -> ExtractedDocument:
    from docx import Document
    from docx.table import Table
    from docx.text.paragraph import Paragraph

    _check_docx_archive(content)
    try:
        document = Document(io.BytesIO(content))
    except Exception as exc:  # python-docx raises a variety of parser errors
        raise ExtractionError("The Word document couldn't be read. It may be damaged.") from exc

    blocks: list[TextBlock] = []
    heading: str | None = None
    for item in document.iter_inner_content():
        if isinstance(item, Paragraph):
            text = normalize_text(item.text)
            if not text:
                continue
            style = (item.style.name if item.style is not None else "") or ""
            if style.startswith("Heading") or style == "Title":
                heading = text
                blocks.append(TextBlock(text, heading=heading, is_heading=True))
            else:
                blocks.append(TextBlock(text, heading=heading))
        elif isinstance(item, Table):
            rows = []
            for row in item.rows:
                cells = [normalize_text(cell.text) for cell in row.cells]
                if any(cells):
                    rows.append(" | ".join(cells))
            if rows:
                blocks.append(TextBlock("\n".join(rows), heading=heading))
    return ExtractedDocument(blocks=blocks)


_MD_HEADING = re.compile(r"^\s{0,3}(#{1,6})\s+(.+?)\s*#*\s*$")
_MD_FENCE = re.compile(r"^\s{0,3}(```|~~~)")


def _extract_markdown(text: str) -> ExtractedDocument:
    """Paragraphs under their nearest ``#`` heading. Headings inside fenced code
    blocks are code, not structure."""
    blocks: list[TextBlock] = []
    heading: str | None = None
    buffer: list[str] = []
    in_fence = False

    def flush() -> None:
        if buffer:
            blocks.extend(_paragraphs("\n".join(buffer), heading=heading))
            buffer.clear()

    for line in text.splitlines():
        if _MD_FENCE.match(line):
            in_fence = not in_fence
            buffer.append(line)
            continue
        match = None if in_fence else _MD_HEADING.match(line)
        if match:
            flush()
            heading = normalize_text(match.group(2))
            blocks.append(TextBlock(heading, heading=heading, is_heading=True))
        else:
            buffer.append(line)
    flush()
    return ExtractedDocument(blocks=blocks)
