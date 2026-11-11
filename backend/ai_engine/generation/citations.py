"""Check and resolve the citations in a generated answer.

The model cites passages by number, like ``[2]``. Only numbers of passages it was
given are kept; any other number (an invented reference) is removed from the text
and reported. Each kept citation is tied to the claim it supports: the span of
answer text between the previous sentence boundary (or citation) and the marker.
"""

import re
from dataclasses import dataclass

from ai_engine.generation.prompt import NOT_FOUND_ANSWER

# [1], [1, 2], [1][2], [1 and 3], [passage 2], [Source 1]
_MARKER = re.compile(
    r"\[\s*(?:(?:passages?|sources?)\s*)?(\d{1,4}(?:\s*(?:,|;|&|and)\s*\d{1,4})*)\s*\]",
    re.IGNORECASE,
)
_GROUP = re.compile(r"\[\d+\](?:[ \t]*\[\d+\])*")  # normalized markers, side by side
_BOUNDARY = re.compile(r"[.!?](?=\s)|\n")
_THINKING = re.compile(r"<think>.*?</think>", re.DOTALL | re.IGNORECASE)
# "I couldn't find this in the knowledge base" and close variants.
_DECLINE = re.compile(
    r"\b(couldnt|could not|cannot|cant|can not|unable to|did not|didnt) find\b"
    r".*\b(knowledge base|documents|passages)\b"
)
_DECLINE_MAX_CHARS = len(NOT_FOUND_ANSWER) * 3


@dataclass(frozen=True)
class AnswerCitation:
    passage: int  # 1-based passage number
    start: int  # the cited claim: answer_text[start:end]
    end: int


@dataclass(frozen=True)
class CheckedAnswer:
    text: str  # markers normalized to [n], invented ones removed
    citations: list[AnswerCitation]  # in reading order
    invalid: list[int]  # cited numbers that weren't among the passages
    declined: bool  # the model said the documents don't answer the question

    @property
    def cited_passages(self) -> list[int]:
        return sorted({c.passage for c in self.citations})


def _normalize_markers(text: str, passage_count: int, invalid: list[int]) -> str:
    def rewrite(match: re.Match[str]) -> str:
        kept: list[int] = []
        for number in (int(n) for n in re.findall(r"\d+", match.group(1))):
            if 1 <= number <= passage_count:
                if number not in kept:
                    kept.append(number)
            elif number not in invalid:
                invalid.append(number)
        return "".join(f"[{n}]" for n in kept)

    return _MARKER.sub(rewrite, text)


def _tidy(text: str) -> str:
    """Whitespace left behind by removed markers ("30 days [9]." -> "30 days.")."""
    text = re.sub(r"[ \t]+([.,;:!?])", r"\1", text)
    return re.sub(r"[ \t]{2,}", " ", text)


def _claim_start(text: str, marker_start: int, previous_end: int) -> int:
    end = marker_start
    while end > previous_end and text[end - 1] in " \t.!?":
        end -= 1  # the cited sentence's own terminator belongs to the claim
    start = previous_end
    for boundary in _BOUNDARY.finditer(text, previous_end, end):
        start = boundary.end()
    while start < end and text[start].isspace():
        start += 1
    return start


def _plain(text: str) -> str:
    text = _GROUP.sub(" ", text).lower()
    text = text.replace("\N{RIGHT SINGLE QUOTATION MARK}", "").replace("'", "")
    return re.sub(r"[^a-z]+", " ", text).strip()


def check_answer(raw: str, passage_count: int) -> CheckedAnswer:
    """Keep the citations that point at one of the ``passage_count`` passages."""
    invalid: list[int] = []
    text = _THINKING.sub("", raw).strip()
    normalized = _normalize_markers(text, passage_count, invalid)
    text = _tidy(normalized).strip() if normalized != text else text

    citations: list[AnswerCitation] = []
    previous_end = 0
    for group in _GROUP.finditer(text):
        start = _claim_start(text, group.start(), previous_end)
        end = group.start()
        while end > start and text[end - 1] in " \t":
            end -= 1
        for number in (int(n) for n in re.findall(r"\d+", group.group())):
            citations.append(AnswerCitation(passage=number, start=start, end=end))
        previous_end = group.end()

    declined = (
        not citations
        and len(text) <= _DECLINE_MAX_CHARS
        and _DECLINE.search(_plain(text)) is not None
    )
    return CheckedAnswer(text=text, citations=citations, invalid=invalid, declined=declined)
