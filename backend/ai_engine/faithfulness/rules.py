"""Deterministic checks that a statement says what its source says (research feature F6).

No model: each rule looks for one kind of change between a statement and the source
sentence it restates (``quote``), and may consult the surrounding text (``context``,
such as the rest of the ticket or document section) only to accept a name or a party
the statement brings in, like "the Q3" for a reply that says "it".

- **quantity**: a number in the statement must be in the quote. If it measures
  something (minutes, days, %, dollars, GB ...), it must measure the same thing there,
  and a number counting something the quote counts with another number is a swap
  ("5 cameras ... 4 devices" for "4 cameras ... 5 devices"). Numbers written as words
  count ("two minutes").
- **name**: product names, plan names and codes in the statement (capitalized phrases,
  "Q3", "E-4012") must appear in the quote or the context. The organization's own name
  counts as present wherever the source says "we".
- **polarity**: each clause of the statement must have the same number of negations,
  modulo 2, as the clause of the quote it restates (the one sharing most words).
  Bounds such as "no more than" and an answering "No," don't count.
- **modality**: an open question or a hedge ("I'll get back to you", "may", "soon")
  must not appear in only one of them; an obligation must not be added where the quote
  gives a permission or a condition, nor an obligation turned into an option.
- **role**: a specific party (buyer, seller, support, carrier ...) the statement names
  must appear in the quote or the context.

These are cheap and precise where they apply; they can't tell who does what to whom
when both parties are named (a reversed direction). That is what NLI or a model judge
is for.
"""

import re
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Literal

_CURLY_APOSTROPHE = chr(0x2019)  # the typographic apostrophe, as in can't

Rule = Literal["quantity", "name", "polarity", "modality", "role"]
RULES: tuple[Rule, ...] = ("quantity", "name", "polarity", "modality", "role")
SELF_NAMES: tuple[str, ...] = ("quillon",)  # the organization: "we" in its own texts


@dataclass(frozen=True)
class Finding:
    rule: Rule
    detail: str


def _plain(text: str) -> str:
    return text.replace(_CURLY_APOSTROPHE, "'")


# --- Quantities ---------------------------------------------------------------------------

# "one" is left out: it is far more often a pronoun ("delay one") than a count.
_WORD_NUMBERS = {
    "zero": 0, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7,
    "eight": 8, "nine": 9, "ten": 10, "eleven": 11, "twelve": 12, "fifteen": 15,
    "twenty": 20, "thirty": 30, "forty": 40, "fifty": 50, "sixty": 60, "ninety": 90,
    "hundred": 100,
}  # fmt: skip
# Words skipped when finding what a number counts ("up to 5 devices", "$9.99 a month").
_FILLER = {
    "a", "an", "the", "per", "of", "to", "up", "and", "or", "than", "at", "about",
    "around", "roughly", "approximately", "only", "just", "more", "most", "least", "m",
}  # fmt: skip
# What a number can measure; a different measure for the same number is a change.
_MEASURES = {
    "second", "minute", "hour", "day", "week", "month", "year", "business", "calendar",
    "%", "$", "dollar", "cent", "gb", "tb", "mb", "mbps", "gbps", "ghz", "metre", "meter",
    "room", "v", "p", "a.m", "p.m",
}  # fmt: skip
_TOKEN = re.compile(r"\$?\d+(?:[.,]\d+)*(?:%|[A-Za-z]+)?|[A-Za-z]+(?:-[A-Za-z]+)*|%")


def _singular(word: str) -> str:
    word = word.lower()
    if len(word) > 3 and word.endswith("ies"):
        return word[:-3] + "y"
    if len(word) > 3 and word.endswith("s") and not word.endswith("ss"):
        return word[:-1]
    return word


def _tokens(text: str) -> list[str]:
    # Codes such as Q3, QC2, E-4012 and Wi-Fi 6E carry digits but aren't quantities.
    text = re.sub(r"\b[A-Za-z]+-?\d+[A-Za-z]*\b", " ", _plain(text))
    text = re.sub(r"\bWi-Fi\s+\d+[A-Za-z]*", " ", text)
    text = re.sub(r"\b([ap])\.m\.", r" \1.m ", text, flags=re.IGNORECASE)
    return _TOKEN.findall(text)


def _split_number(token: str) -> tuple[float, str] | None:
    """A token's value and any unit written onto it ("15%", "1080p", "$9.99")."""
    lowered = token.lower()
    for part in lowered.split("-"):  # "two-year"
        if part in _WORD_NUMBERS:
            unit = lowered.split("-", 1)[1] if "-" in lowered else ""
            return float(_WORD_NUMBERS[part]), _singular(unit) if unit else ""
    match = re.fullmatch(r"(\$)?(\d+(?:[.,]\d+)*)(%|[a-z]+)?", lowered)
    if not match:
        return None
    value = float(match.group(2).replace(",", ""))
    return value, match.group(3) or ("$" if match.group(1) else "")


def quantities(text: str) -> set[tuple[float, str]]:
    """(number, what it counts or measures) for every number, in digits or in words."""
    tokens = _tokens(text)
    found: set[tuple[float, str]] = set()
    for i, token in enumerate(tokens):
        parsed = _split_number(token)
        if parsed is None:
            continue
        value, unit = parsed
        if unit in ("", "$"):
            money = unit
            unit = ""
            for following in tokens[i + 1 : i + 5]:
                if _split_number(following) is not None or following.lower() in _FILLER:
                    continue
                unit = "%" if following == "%" else _singular(following)
                break
            if money and unit not in _MEASURES:
                unit = money
        found.add((value, unit))
    return found


def _check_quantities(statement: str, quote: str) -> list[Finding]:
    stated, sourced = quantities(statement), quantities(quote)
    values = {v for v, _ in sourced}
    units_elsewhere = {u: {v for v, w in sourced if w == u} for _, u in sourced}
    findings = []
    for value, unit in sorted(stated):
        if (value, unit) in sourced:
            continue
        shown = f"{value:g}" + (f" {unit}" if unit else "")
        if value not in values:
            findings.append(Finding("quantity", f"{shown} is not in the quote"))
        elif unit in _MEASURES:
            findings.append(Finding("quantity", f"{shown}: the quote measures {value:g} otherwise"))
        elif unit and units_elsewhere.get(unit):
            findings.append(Finding("quantity", f"{shown}: the quote counts {unit} differently"))
    return findings


# --- Names and codes ------------------------------------------------------------------------

_CODE = re.compile(r"\b[A-Z]{1,4}-?\d+[A-Za-z]?\b")
_CAPITALIZED_RUN = re.compile(r"(?:[A-Z][\w-]*)(?:\s+[A-Z][\w-]*)*")
_SENTENCE_START = re.compile(r"(?:^|[.!?:;]\s+|\(\s*)$")
_ABBREVIATION_END = re.compile(r"\b(?:[ap]\.m|e\.g|i\.e|etc)\.\s+$", re.IGNORECASE)
_CODE_DIGITS = re.compile(r"[A-Za-z]+-?\d")
_WE = re.compile(r"\b(?:we|our|us)\b", re.IGNORECASE)
# Capitalized words that aren't names (headings and UI words are checked as written).
_COMMON = {"i", "if", "a", "an", "the", "to", "for", "with", "when", "in", "on", "after", "before"}


def names(text: str) -> set[str]:
    """Product names, plan names and codes: codes, and capitalized phrases (a phrase
    that starts a sentence loses its first word, which is capitalized anyway)."""
    found = {m.group(0).lower() for m in _CODE.finditer(text)}
    for m in _CAPITALIZED_RUN.finditer(text):
        words = m.group(0).split()
        before = text[: m.start()]
        if _SENTENCE_START.search(before) and not _ABBREVIATION_END.search(before):
            words = words[1:]
        words = [w for w in words if w.lower() not in _COMMON]
        if words:
            phrase = " ".join(words).lower()
            if not _CODE_DIGITS.fullmatch(phrase):
                found.add(phrase)
    return found


def _present(name: str, source: str, self_names: Sequence[str]) -> bool:
    if name in re.sub(r"\s+", " ", source.lower()):
        return True
    return name in self_names and bool(_WE.search(source))


def _check_names(
    statement: str, quote: str, context: str, self_names: Sequence[str]
) -> list[Finding]:
    source = f"{quote}\n{context}"
    return [
        Finding("name", f"'{name}' is not in the source")
        for name in sorted(names(statement))
        if not _present(name, source, self_names)
    ]


# --- Polarity -----------------------------------------------------------------------------------

_NEGATION = re.compile(
    r"\b(?:not|no|never|none|nothing|nobody|neither|nor|without|cannot)\b|n't\b"
    r"|\bun(?:available|able|supported|limited)\b",
    re.IGNORECASE,
)
_NOT_A_NEGATION = re.compile(
    r"\bno\s+(?:more|later|earlier|fewer|less)\s+than\b|\bno\s+problem\b"
    r"|\bno\s+questions\s+asked\b|^\s*(?:no|yes)\s*,",
    re.IGNORECASE,
)
# A full stop between digits ("2.4 GHz") or in "a.m." doesn't end a clause.
_CLAUSE_BREAK = re.compile(
    r"(?<!\d)\.(?!\d)|[;:!?]|,\s*(?:but|because|so|since|and then|then)\b|\s+but\s+"
)
_STOP = {
    "the", "a", "an", "and", "or", "of", "to", "in", "on", "for", "with", "is", "are", "be",
    "it", "its", "your", "you", "this", "that", "can", "will", "not", "no", "do", "does",
}  # fmt: skip


def negations(text: str) -> int:
    return len(_NEGATION.findall(_NOT_A_NEGATION.sub(" ", _plain(text))))


def _clauses(text: str) -> list[str]:
    text = re.sub(r"\b([ap])\.m\.", r"\1m", _plain(text), flags=re.IGNORECASE)
    return [c for c in _CLAUSE_BREAK.split(text) if c and c.strip()]


def _stem(word: str) -> str:
    word = _singular(word)
    for suffix in ("ing", "ed"):
        if len(word) > len(suffix) + 3 and word.endswith(suffix):
            return word[: -len(suffix)]
    return word


def _content(text: str) -> set[str]:
    words = re.findall(r"[a-z0-9]+", text.lower())
    return {_stem(w) for w in words if w not in _STOP and len(w) > 1}


def _check_polarity(statement: str, quote: str) -> list[Finding]:
    """Compare the statement's negations with those of the part of the quote it
    restates: for each clause of the statement, the quote's clause sharing most words
    with it, and any quote clause that shares half its words with one of them (a
    statement can restate only part of a quote, or merge two of its clauses)."""
    quote_clauses = _clauses(quote) or [quote]
    covered: list[str] = []
    for clause in _clauses(statement) or [statement]:
        words = _content(clause)
        if not words:
            continue
        best = max(quote_clauses, key=lambda q: len(words & _content(q)) / len(words | _content(q)))
        for q in quote_clauses:
            shared = len(words & _content(q))
            if (q is best or shared >= max(1, 0.5 * len(_content(q)))) and q not in covered:
                covered.append(q)
    stated = negations(statement)
    sourced = sum(negations(q) for q in covered)
    if stated % 2 == sourced % 2:
        return []
    return [Finding("polarity", f"{stated} negations against {sourced} in the restated quote")]


# --- Modality -----------------------------------------------------------------------------------

_OPEN = re.compile(
    r"\b(?:get back to|find out|asked (?:our|the) \w+ team|(?:has|have) been asked|"
    r"looking into|investigating|awaiting|being (?:confirmed|considered|investigated)|"
    r"not sure|may|might|could|possibly|soon|plans? to|in a future|"
    r"should (?:be|support|work)|depending on)\b",
    re.IGNORECASE,
)
_OBLIGATION = re.compile(r"\b(?:must|have to|has to|had to|need to|needs to|required to)\b", re.I)
_NO_OBLIGATION = re.compile(
    r"\b(?:do|does|did)(?:n't| not) (?:have|need) to\b|\bno need to\b|\bnot required to\b", re.I
)
_PERMISSION = re.compile(r"\bcan\b(?!'t| not)|\bmay\b|\bable to\b|\bif you\b|\boptional", re.I)


def _obliged(text: str) -> bool:
    return bool(_OBLIGATION.search(_NO_OBLIGATION.sub(" ", _plain(text))))


def _check_modality(statement: str, quote: str) -> list[Finding]:
    findings = []
    open_stated, open_sourced = bool(_OPEN.search(statement)), bool(_OPEN.search(quote))
    if open_stated != open_sourced:
        findings.append(
            Finding(
                "modality",
                "adds a hedge or a promise"
                if open_stated
                else "states as settled what the quote leaves open",
            )
        )
    must_stated, must_sourced = _obliged(statement), _obliged(quote)
    if must_stated and not must_sourced and _PERMISSION.search(_plain(quote)):
        findings.append(Finding("modality", "turns a permission or a condition into an obligation"))
    elif must_sourced and not must_stated and re.search(r"\b(?:may|optional)\b", statement, re.I):
        findings.append(Finding("modality", "turns an obligation into an option"))
    return findings


# --- Roles ---------------------------------------------------------------------------------------

# Parties a statement can wrongly bring in. Generic ones ("you", "the customer") are not
# checked: restatements address the reader in many ways.
_ROLES = {
    "buyer": r"buyers?",
    "seller": r"sellers?",
    "new owner": r"new owners?",
    "support": r"support(?: agents?| team)?|agents?",
    "engineering": r"engineers?|engineering(?: team)?",
    "carrier": r"carriers?|couriers?",
    "retailer": r"retailers?|(?:another|that|the|a|an|electronics) (?:store|shop)s?",
    "household member": r"household members?",
}


def _check_roles(
    statement: str, quote: str, context: str, self_names: Sequence[str]
) -> list[Finding]:
    source = f"{quote}\n{context}"
    findings = []
    for role, pattern in _ROLES.items():
        regex = re.compile(rf"\b(?:{pattern})\b", re.IGNORECASE)
        if regex.search(statement) and not regex.search(source):
            findings.append(Finding("role", f"names the {role}, which the source doesn't"))
    for name in self_names:
        # The organization acting where the source has someone else act.
        acting = re.compile(rf"\b{re.escape(name)}\b(?! (?:home|secure|product|warranty))", re.I)
        present = re.search(rf"\b{re.escape(name)}\b", source, re.I) or _WE.search(source)
        if acting.search(statement) and not present:
            findings.append(Finding("role", f"names {name}, which the source doesn't"))
    return findings


# --- All rules -----------------------------------------------------------------------------------


def check(
    statement: str, quote: str, context: str = "", self_names: Sequence[str] = SELF_NAMES
) -> list[Finding]:
    """Every rule's findings (empty: no rule objects)."""
    return [
        *_check_quantities(statement, quote),
        *_check_names(statement, quote, context, self_names),
        *_check_polarity(statement, quote),
        *_check_modality(statement, quote),
        *_check_roles(statement, quote, context, self_names),
    ]
