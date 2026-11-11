"""Split a document into overlapping, token-bounded chunks with provenance.

Algorithm:

1. Join the blocks into one text (blank line between blocks) and remember which
   character range came from which page and section.
2. Split each block into sentence-like units; measure each in tokens. A unit
   longer than the chunk size is cut at token boundaries.
3. Within each section, pack consecutive units greedily up to ``size`` tokens.
   The next chunk starts with whole trailing units of the previous one totalling
   at most ``overlap`` tokens. Chunks never span two sections, so a chunk is about
   one topic and its heading is accurate.

Each chunk is an exact slice ``text[start:end]`` of the joined text, so offsets
can be mapped back to the source, and records the page range it covers.
"""

import re
from dataclasses import dataclass

from ai_engine.chunking.tokenizer import TokenCounter
from ai_engine.ingestion.extract import TextBlock

BLOCK_SEPARATOR = "\n\n"
# Sentence ends: punctuation, then whitespace, then something that starts a sentence.
_SENTENCE_END = re.compile(r"(?<=[.!?])\s+(?=[\"'(\[]?[A-Z0-9])")


@dataclass(frozen=True)
class ChunkingConfig:
    size: int  # maximum tokens per chunk
    overlap: int  # tokens repeated from the end of the previous chunk

    def __post_init__(self) -> None:
        if self.size < 1 or self.overlap < 0 or self.overlap >= self.size:
            raise ValueError("Need 0 <= overlap < size")


@dataclass(frozen=True)
class Chunk:
    index: int
    text: str
    start: int  # offsets into the joined document text
    end: int
    page: int | None  # first page the chunk covers
    page_end: int | None  # last page the chunk covers
    heading: str | None
    token_count: int


@dataclass(frozen=True)
class _Unit:
    start: int
    end: int
    page: int | None
    section: int  # sections are numbered; a heading starts a new one
    heading: str | None
    tokens: int = 0


def join_blocks(blocks: list[TextBlock]) -> str:
    return BLOCK_SEPARATOR.join(block.text for block in blocks)


def _sentence_spans(text: str) -> list[tuple[int, int]]:
    """Sentence-like spans of a block: split at sentence ends and line breaks."""
    spans: list[tuple[int, int]] = []
    for line in re.finditer(r"[^\n]+", text):
        cursor = line.start()
        for boundary in _SENTENCE_END.finditer(line.group()):
            end = line.start() + boundary.start()
            spans.append((cursor, end))
            cursor = line.start() + boundary.end()
        spans.append((cursor, line.end()))
    return [(s, e) for s, e in spans if text[s:e].strip()]


def _units(blocks: list[TextBlock]) -> tuple[str, list[_Unit]]:
    text = join_blocks(blocks)
    units: list[_Unit] = []
    offset = 0
    section = 0
    previous_heading: str | None = None
    for block in blocks:
        if block.is_heading or block.heading != previous_heading:
            section += 1
            previous_heading = block.heading
        for start, end in _sentence_spans(block.text):
            units.append(_Unit(offset + start, offset + end, block.page, section, block.heading))
        offset += len(block.text) + len(BLOCK_SEPARATOR)
    return text, units


def _split_oversized(text: str, unit: _Unit, counter: TokenCounter, size: int) -> list[_Unit]:
    """Cut a unit longer than ``size`` tokens into pieces at token boundaries."""
    spans = counter.token_spans(text[unit.start : unit.end])
    pieces = []
    for i in range(0, len(spans), size):
        window = spans[i : i + size]
        pieces.append(
            _Unit(
                unit.start + window[0][0],
                unit.start + window[-1][1],
                unit.page,
                unit.section,
                unit.heading,
                len(window),
            )
        )
    return pieces


def chunk_blocks(
    blocks: list[TextBlock], counter: TokenCounter, config: ChunkingConfig
) -> list[Chunk]:
    text, raw_units = _units(blocks)
    counts = counter.count([text[u.start : u.end] for u in raw_units])
    units: list[_Unit] = []
    for unit, tokens in zip(raw_units, counts, strict=True):
        if tokens > config.size:
            units.extend(_split_oversized(text, unit, counter, config.size))
        elif tokens > 0:
            units.append(_Unit(unit.start, unit.end, unit.page, unit.section, unit.heading, tokens))

    windows: list[tuple[int, int]] = []  # [first, last] unit index of each chunk
    i = 0
    while i < len(units):
        section = units[i].section
        total = 0
        j = i
        while (
            j < len(units)
            and units[j].section == section
            and total + units[j].tokens <= config.size
        ):
            total += units[j].tokens
            j += 1
        windows.append((i, j - 1))
        if j >= len(units) or units[j].section != section:
            i = j  # a new section starts fresh, without overlap
            continue
        # Overlap: step back over whole trailing units worth at most `overlap` tokens.
        k, carried = j, 0
        while k - 1 > i and carried + units[k - 1].tokens <= config.overlap:
            k -= 1
            carried += units[k].tokens
        i = k

    slices = [(units[a].start, units[b].end) for a, b in windows]
    exact = counter.count([text[start:end] for start, end in slices])
    return [
        Chunk(
            index=n,
            text=text[start:end],
            start=start,
            end=end,
            page=units[a].page,
            page_end=units[b].page,
            heading=units[a].heading,
            token_count=tokens,
        )
        for n, ((a, b), (start, end), tokens) in enumerate(zip(windows, slices, exact, strict=True))
    ]
