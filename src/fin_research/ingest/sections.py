"""Split a 10-K into its numbered Items, so an agent can be handed Risk Factors rather than the whole filing.

This is the hardest part of the ingestion layer, because a filing mentions its own item numbers constantly and
only a handful of those mentions are actually headings. In one Apple 10-K the string 'Item 1A' appears five
times: once in the table of contents, once as the real heading, and three times as cross-references inside
sentences. Picking the wrong one hands an agent the table of contents and every citation drawn from it is
worthless, so the failure has to be impossible rather than unlikely.

Three independent signals are combined, each killing a different impostor:

  1. Line anchoring kills mid-sentence cross-references. A heading starts its line; 'see Item 1A. Risk Factors
     in this report' does not.
  2. Internal-link exclusion kills the table of contents. A contents row is an `<a href="#...">` by
     construction and a real heading never is -- this is what `text.clean` records link spans for.
  3. A longest-increasing-subsequence pass kills whatever survives. Real headings partition the document in
     ascending item order exactly once; leftover references do not fit that ordering.

A density heuristic was tried first and rejected: a window wide enough to catch a contents block also
swallowed Item 1, which sits a couple of thousand characters after it, and all of Part III, whose eight
one-line items are legitimately dense.
"""

import logging
import re
from dataclasses import dataclass

from fin_research.ingest.text import CleanedDocument, estimate_tokens

log = logging.getLogger(__name__)

# The marker, not the title, is anchored: titles run to 115 characters in real filings, so capping their
# length silently loses Items 5, 7, 9, 12 and 13.
# Built from codepoints so the source stays ASCII; filings punctuate headings with en and em dashes.
_DASHES = ''.join(map(chr, (0x2013, 0x2014)))
_RSQUO = chr(0x2019)

ITEM_HEADING = re.compile(
    rf'(?im)^[ \t|]*(?P<marker>item\s+(?P<num>\d{{1,2}})\s*(?P<suffix>[A-C])?\s*[.:)\-{_DASHES}]?)'
    r'[ \t]*(?P<title>[^\n]*)'
)

# Canonical order of a 10-K's items. Membership defines what counts as an item; position defines the ordering
# the subsequence pass enforces.
ITEM_ORDER = ('1', '1A', '1B', '1C', '2', '3', '4', '5', '6', '7', '7A', '8',
              '9', '9A', '9B', '9C', '10', '11', '12', '13', '14', '15', '16')
ITEM_RANK = {key: index for index, key in enumerate(ITEM_ORDER)}

ITEM_TITLES = {
    '1': 'business', '1A': 'risk factors', '1B': 'unresolved staff comments', '1C': 'cybersecurity',
    '2': 'properties', '3': 'legal proceedings', '4': 'mine safety', '5': 'market for registrant',
    '6': 'reserved', '7': "management's discussion", '7A': 'quantitative and qualitative',
    '8': 'financial statements', '9': 'changes in and disagreements', '9A': 'controls and procedures',
    '9B': 'other information', '9C': 'disclosure regarding foreign', '10': 'directors',
    '11': 'executive compensation', '12': 'security ownership', '13': 'certain relationships',
    '14': 'principal accountant', '15': 'exhibit', '16': 'form 10-k summary',
}

# The items an analyst actually reads. Their absence means the split failed, whatever the rest looks like.
REQUIRED_ITEMS = ('1', '1A', '7', '8')

# ITEM_ORDER describes an annual report. A 10-Q numbers its items separately within Part I and Part II, so
# Item 1 occurs twice and the ascending-order assumption this module rests on simply does not hold. Rather
# than return a confident-looking wrong answer, quarterly filings are refused here.
SUPPORTED_FORMS = frozenset({'10-K', '10-K/A', '10-K405', '10-KSB'})

# Below this length an item is almost certainly a pointer rather than the content itself.
REDIRECT_MAX_CHARS = 2000
REDIRECT_MARKERS = ('incorporated herein by reference', 'incorporated by reference', 'separate section',
                    'included beginning on page', 'set forth under', 'are included in', 'appearing on page',
                    'appears on page', 'appear on page', 'presented on page', 'beginning on page',
                    'refer to', 'see the', 'is contained in', 'are contained in')

# A split whose sections are this short is describing the filing rather than being it.
CONTENTS_SECTION_CHARS = 600
_MIN_CONTENTS_ROWS = 6

# A 10-K with this many distinct items looks structurally complete for any era.
_COMPLETE_ITEM_COUNT = 15
_MAX_SPLIT_ATTEMPTS = 3

_TITLE_BONUS = 0.5


@dataclass(frozen=True)
class Section:
    """One item of a filing, as a span of the document's canonical text.

    `is_redirect` and `redirect_to` are separate on purpose. A filer often satisfies an item with a pointer to
    somewhere else -- 'appears on pages 46-160' names no item at all -- so "this is a pointer" and "here is
    what it points at" are genuinely different facts, and collapsing them would make an unnamed redirect
    indistinguishable from real content.
    """

    item_key: str
    title: str
    char_start: int
    char_end: int
    method: str
    is_redirect: bool = False
    redirect_to: str | None = None

    @property
    def n_chars(self) -> int:
        """Length of the section in characters."""
        return self.char_end - self.char_start

    @property
    def est_tokens(self) -> int:
        """Rough token count for budgeting."""
        return estimate_tokens('x' * self.n_chars)

    def text(self, document_text: str) -> str:
        """The section's text, sliced out of the document it was measured against."""
        return document_text[self.char_start:self.char_end]


@dataclass(frozen=True)
class SplitResult:
    """The sections found, plus whether the split is trustworthy enough to cite from."""

    sections: tuple[Section, ...]
    method: str
    confidence: float

    def by_item(self, item_key: str) -> Section | None:
        """The section for `item_key`, or None if the split did not find it."""
        return next((s for s in self.sections if s.item_key == item_key), None)


@dataclass(frozen=True)
class _Candidate:
    """A possible heading: where it is, which item it claims to be, and how heading-like it looks."""

    position: int
    item_key: str
    title: str
    weight: float


def _normalise_key(num: str, suffix: str | None) -> str:
    """Canonical item key, e.g. ('1', 'a') -> '1A'."""
    return f'{num}{(suffix or "").upper()}'


def find_candidates(cleaned: CleanedDocument) -> list[_Candidate]:
    """Line-anchored item markers that are not part of the table of contents.

    Weighting is deliberately gentle: a title that matches the canonical one is evidence, not proof, since
    some filers bury the title on the following line.
    """
    candidates: list[_Candidate] = []
    for match in ITEM_HEADING.finditer(cleaned.text):
        item_key = _normalise_key(match.group('num'), match.group('suffix'))
        if item_key not in ITEM_RANK:
            continue

        position = match.start('marker')
        if cleaned.in_link(position):
            continue

        raw_title = match.group('title')
        # A contents row puts the marker in one cell and its title in the next, so the marker's line reads
        # 'Item 1. | Business'. A heading laid out in a table reads 'Item 1. |' with the title wrapping to the
        # following line, and a plain heading reads 'Item 1. Business'. So it is specifically a cell separator
        # *followed by title text on the same line* that identifies a contents row -- the separator alone does
        # not, and neither does an empty title. Both of those weaker rules were tried and each destroyed a
        # different filer's real headings.
        stripped = raw_title.lstrip()
        if stripped.startswith('|') and stripped.strip(' |').strip():
            continue

        title = raw_title.strip(' |').strip()
        expected = ITEM_TITLES.get(item_key, '')
        comparable = title.lower().replace(_RSQUO, "'")
        weight = 1.0 + (_TITLE_BONUS if expected and comparable.startswith(expected[:12]) else 0.0)
        candidates.append(_Candidate(position=position, item_key=item_key, title=title, weight=weight))

    return candidates


def longest_increasing_run(candidates: list[_Candidate]) -> list[_Candidate]:
    """Heaviest chain of candidates whose item order strictly increases with position.

    Real headings partition the document in ascending item order exactly once, so the true set is a strictly
    increasing subsequence. Maximising total weight rather than plain length makes title-matching candidates
    win ties, which is what separates a genuine heading from a stray reference at the same item number.
    """
    if not candidates:
        return []

    ordered = sorted(candidates, key=lambda c: c.position)
    ranks = [ITEM_RANK[c.item_key] for c in ordered]
    best = [c.weight for c in ordered]
    previous: list[int | None] = [None] * len(ordered)

    for i in range(len(ordered)):
        for j in range(i):
            if ranks[j] < ranks[i] and best[j] + ordered[i].weight > best[i]:
                best[i] = best[j] + ordered[i].weight
                previous[i] = j

    index: int | None = max(range(len(ordered)), key=lambda i: best[i])
    chain: list[_Candidate] = []
    while index is not None:
        chain.append(ordered[index])
        index = previous[index]
    return list(reversed(chain))


def _redirect_target(text: str, own_key: str) -> str | None:
    """Item number a short pointer section defers to, or '' for an unnamed redirect, or None if not one.

    Filers routinely satisfy Item 8 with 'submitted as a separate section of this report immediately following
    Item 15'. Treating that 160-character pointer as the financial statements would hand an agent nothing.
    """
    lowered = text.lower()
    if not any(marker in lowered for marker in REDIRECT_MARKERS):
        return None

    # Skip the heading line, or the section's own marker reads as its own target.
    newline = lowered.find('\n')
    body = lowered[newline + 1:] if newline >= 0 else lowered
    for pointed in re.finditer(r'item\s+(\d{1,2})\s*([a-c])?', body):
        key = _normalise_key(pointed.group(1), pointed.group(2))
        if key != own_key:
            return key
    return ''


def _build_sections(chain: list[_Candidate], text: str) -> list[Section]:
    """Turn a chain of headings into spans, flagging any item satisfied by a pointer elsewhere."""
    sections: list[Section] = []
    for position, candidate in enumerate(chain):
        end = chain[position + 1].position if position + 1 < len(chain) else len(text)
        body = text[candidate.position:end]
        redirect = _redirect_target(body, candidate.item_key) if len(body) < REDIRECT_MAX_CHARS else None
        sections.append(Section(item_key=candidate.item_key, title=candidate.title,
                                char_start=candidate.position, char_end=end, method='anchor_lis',
                                is_redirect=redirect is not None, redirect_to=redirect or None))
    return sections


def _contents_block_end(sections: list[Section]) -> int | None:
    """Offset just past a leading run of contents rows, or None if these sections look like real content.

    A contents block is a long run of near-empty sections followed by one that swallows the entire rest of the
    document. Real items are not shaped like that, even when several of them are genuinely short.
    """
    if len(sections) < _MIN_CONTENTS_ROWS:
        return None

    leading = 0
    while leading < len(sections) and sections[leading].n_chars < CONTENTS_SECTION_CHARS:
        leading += 1

    if leading < _MIN_CONTENTS_ROWS or leading == len(sections):
        return None
    return sections[leading - 1].char_end


def _whole_document(cleaned: CleanedDocument, reason: str) -> SplitResult:
    """Fall back to one unsplit section, so callers get too much text rather than the wrong text."""
    log.warning(f'{reason}; falling back to the whole document')
    return SplitResult(sections=(Section('whole', '', 0, len(cleaned.text), 'whole_doc'),),
                       method='whole_doc', confidence=0.0)


def split(cleaned: CleanedDocument, form: str = '10-K') -> SplitResult:
    """Split an annual report's canonical text into its numbered items.

    On failure the whole document is returned as a single section with zero confidence rather than a plausible
    but wrong split. An agent reading too much is affordable; an agent citing the table of contents is not.
    """
    if form not in SUPPORTED_FORMS:
        return _whole_document(cleaned, f'{form} is not an annual report and does not use 10-K item numbering')

    candidates = find_candidates(cleaned)
    sections: list[Section] = []

    # A contents block that is neither hyperlinked nor cell-separated still forms a complete, correctly
    # ordered chain and can outweigh the real one. It gives itself away by section length, so on detecting it
    # the block is skipped past and the search repeats on the remainder.
    for _ in range(_MAX_SPLIT_ATTEMPTS):
        chain = longest_increasing_run(candidates)
        if not chain:
            return _whole_document(cleaned, 'no item headings found')

        sections = _build_sections(chain, cleaned.text)
        cutoff = _contents_block_end(sections)
        if cutoff is None:
            break

        log.info(f'chain up to offset {cutoff} looks like a table of contents; searching past it')
        candidates = [c for c in candidates if c.position >= cutoff]
    else:
        return _whole_document(cleaned, 'every candidate chain looks like a table of contents')

    found = {s.item_key for s in sections}
    missing = [item for item in REQUIRED_ITEMS if item not in found]
    covered = sections[-1].char_end - sections[0].char_start
    coverage = covered / max(len(cleaned.text) - sections[0].char_start, 1)

    # Scored against a plausible floor rather than against ITEM_ORDER's full length, because the list has
    # grown over time -- Items 1C, 9C and 16 postdate plenty of filings that are split perfectly well. Judging
    # a 2015 filing against a 2025 item list marks correct work as doubtful.
    confidence = (0.3 if missing else 1.0) * min(coverage / 0.6, 1.0) * min(len(found) / _COMPLETE_ITEM_COUNT, 1.0)
    if missing:
        log.warning(f'split is missing required item(s) {missing}; confidence {confidence:.2f}')

    return SplitResult(sections=tuple(sections), method='anchor_lis', confidence=round(confidence, 3))
