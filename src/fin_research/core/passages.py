"""Split a source into numbered passages, so a citation can be an identifier instead of a quotation.

Asking a model to copy text back verbatim does not work reliably, and the way it fails is the worst possible
one: it produces an accurate paraphrase. A real filing says "higher net sales in its first quarter" and the
reply says "in its first fiscal quarter" -- one word inserted, the meaning intact, the citation unverifiable.
Tightening the prompt moved that a little. Loosening the matcher would fix it by abandoning the guarantee,
since a fuzzy match accepts invented text as readily as remembered text.

Numbering the source removes the problem rather than mitigating it. The model is shown `[42] ...` and cites
42; the offsets come from the passage table, which the model never touches. A number cannot be paraphrased,
and a number that does not exist is rejected outright.

Quoting still works and is still checked, so an agent that supplies a quote is held to the old standard.
"""

import re
from dataclasses import dataclass

# Sentence-ish boundaries. Filings abbreviate constantly -- "U.S.", "Inc.", "No. 1" -- so a split on every
# full stop produces fragments that are useless to cite. Splitting on a stop followed by whitespace and a
# capital, with common abbreviations protected, is imperfect and good enough: a passage that runs long is
# still citable, while a passage cut mid-abbreviation is not.
_ABBREVIATIONS = ('U.S.', 'U.K.', 'Inc.', 'Corp.', 'Co.', 'Ltd.', 'No.', 'Nos.', 'Mr.', 'Ms.', 'Dr.',
                  'St.', 'Jr.', 'Sr.', 'vs.', 'etc.', 'approx.', 'Sec.', 'Art.')
_GUARD = '\x02'

_BOUNDARY = re.compile(r'(?<=[.!?])\s+(?=[A-Z"“(])|\n{2,}')

# Below this a passage is a table cell or a stray fragment, not something worth citing on its own.
MIN_PASSAGE_CHARS = 40
# Above this a passage is a wall of table data; it stays citable but is truncated when shown to the model.
MAX_SHOWN_CHARS = 400


@dataclass(frozen=True)
class Passage:
    """One citable span of a source, with the offsets it occupies in the canonical text."""

    passage_id: int
    char_start: int
    char_end: int
    text: str

    def shown(self) -> str:
        """The passage as the model sees it, collapsed to one line and trimmed if very long."""
        flat = ' '.join(self.text.split())
        return flat if len(flat) <= MAX_SHOWN_CHARS else flat[:MAX_SHOWN_CHARS] + '...'


def split_passages(text: str) -> list[Passage]:
    """Number a source's passages, preserving where each one sits in the original.

    Offsets are carried rather than recomputed, so a citation resolves to an exact span of the canonical text
    without any matching at all.
    """
    protected = text
    for abbreviation in _ABBREVIATIONS:
        protected = protected.replace(abbreviation, abbreviation.replace('.', _GUARD))

    passages: list[Passage] = []
    cursor = 0
    for piece in _BOUNDARY.split(protected):
        restored = piece.replace(_GUARD, '.')
        start = text.find(restored, cursor) if restored.strip() else -1
        if start < 0:
            cursor += len(piece)
            continue
        cursor = start + len(restored)
        if len(restored.strip()) >= MIN_PASSAGE_CHARS:
            passages.append(Passage(passage_id=len(passages) + 1, char_start=start,
                                    char_end=start + len(restored), text=restored))
    return passages


def render(passages: list[Passage]) -> str:
    """The numbered source, as it is put in front of the model."""
    return '\n'.join(f'[{p.passage_id}] {p.shown()}' for p in passages)


@dataclass(frozen=True)
class PassageIndex:
    """Numbered passages for one document, for resolving citations back to offsets."""

    document_id: str
    passages: tuple[Passage, ...]

    @classmethod
    def build(cls, document_id: str, text: str) -> 'PassageIndex':
        """Number a document's passages."""
        return cls(document_id=document_id, passages=tuple(split_passages(text)))

    def get(self, passage_id: int) -> Passage | None:
        """One passage by its number, or None if the model cited a number that does not exist."""
        if 1 <= passage_id <= len(self.passages):
            return self.passages[passage_id - 1]
        return None

    def render(self) -> str:
        """The numbered source for this document."""
        return render(list(self.passages))

    def __len__(self) -> int:
        """How many passages the document was split into."""
        return len(self.passages)
