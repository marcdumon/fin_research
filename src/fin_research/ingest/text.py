"""Turn filing HTML into the one canonical plain-text rendering every citation offset refers to.

This module defines the coordinate system for the whole project. An evidence span is `(char_start, char_end)`
into the string `clean` returns, so the transformation must be deterministic: the same input HTML has to
produce byte-identical output forever, or previously stored citations silently start pointing at the wrong
words. That rules out anything heuristic, which is why this is an explicit tag walk rather than a
readability-style extractor.

Three filing-specific hazards are handled here. Modern filings are inline XBRL, carrying a hidden header of
machine-readable facts that renders as hundreds of lines of noise. Financial statements are HTML tables, which
collapse into unreadable number soup unless cell boundaries survive, so cells are joined with a pipe. And
filings are riddled with invisible characters -- one real 10-K carries 5,223 zero-width spaces and another
11,021 non-breaking spaces -- which are stripped, because a model echoing a quote back types ASCII and an
invisible character would turn a correct citation into a failed match.

The walk also records which spans of the output came from internal anchor links (`<a href="#...">`). A
table-of-contents row is an internal link by construction and a real section heading never is, which is the
cleanest signal available for telling the two apart downstream.
"""

import re
from dataclasses import dataclass

import lxml.html
from lxml import etree

# Bumped whenever the transformation changes. Stored alongside every document, because an offset is only
# meaningful against the exact string one exact version of this code produced.
CLEANER_VERSION = 'clean/1'

# Dropped whole: content that is never part of the readable filing. 'hidden' and 'header' catch inline-XBRL's
# `ix:hidden` / `ix:header` blocks once namespaces are stripped.
_SKIP_TAGS = frozenset({'script', 'style', 'head', 'hidden', 'header'})

# Emit a line break after these, so paragraph and row structure survives.
_LINE_TAGS = frozenset({'p', 'div', 'tr', 'li', 'table', 'section', 'article', 'br',
                        'h1', 'h2', 'h3', 'h4', 'h5', 'h6'})

# Emit a column separator after these, so statement tables stay readable.
_CELL_TAGS = frozenset({'td', 'th'})

# EDGAR's renderer appends an XBRL element-definition block per line item -- namespace, data type, balance
# type, FASB references. On one balance sheet that is 78 KB of boilerplate wrapped around 1.5 KB of numbers,
# so it is dropped: it is machine metadata that no reader and no analyst would ever quote.
_SKIP_CLASSES = frozenset({'authRefData'})

# Control characters chosen because they cannot occur in filing text and survive whitespace normalisation.
_MARK_OPEN = '\x00'
_MARK_CLOSE = '\x01'
_MARK_SPLIT = re.compile(r'([\x00\x01])')

# Built from codepoints so the source stays ASCII: these characters are invisible and would be
# indistinguishable from a plain space when reading the file.
_ZERO_WIDTH_CHARS = ''.join(map(chr, (0x200b, 0x200c, 0x200d, 0x2060, 0xfeff, 0x00ad)))
_WIDE_SPACE_CHARS = ''.join(map(chr, (0x00a0, 0x2007, 0x202f)))

_ZERO_WIDTH = re.compile(f'[{_ZERO_WIDTH_CHARS}]')
_HORIZONTAL_WS = re.compile(f'[ \\t\\r\\f\\v{_WIDE_SPACE_CHARS}]+')
_TRAILING_WS = re.compile(r'[ \t]+\n')
_BLANK_RUN = re.compile(r'\n{3,}')
_EMPTY_CELLS = re.compile(r'(?:\|\s*){2,}')


@dataclass(frozen=True)
class CleanedDocument:
    """Canonical text plus the offsets of anything that came from an internal link."""

    text: str
    link_spans: tuple[tuple[int, int], ...]
    cleaner_version: str = CLEANER_VERSION

    def in_link(self, position: int) -> bool:
        """True if `position` falls inside a span that was an internal anchor link."""
        return any(start <= position < end for start, end in self.link_spans)


def _localname(tag: object) -> str:
    """Lowercase tag name with any XML namespace stripped, or '' for comments and processing instructions."""
    if not isinstance(tag, str):
        return ''
    return tag.rsplit('}', 1)[-1].lower()


def _is_hidden(element: etree._Element) -> bool:
    """True for content the renderer hides from a reader: XBRL definition blocks and display:none wrappers."""
    classes = (element.get('class') or '').split()
    if any(name in _SKIP_CLASSES for name in classes):
        return True
    return 'display:none' in (element.get('style') or '').replace(' ', '')


def _is_internal_link(element: etree._Element) -> bool:
    """True for `<a href="#...">`, which is what a table-of-contents row is made of."""
    return _localname(element.tag) == 'a' and (element.get('href') or '').startswith('#')


def _walk(element: etree._Element, parts: list[str]) -> None:
    """Append `element`'s readable text to `parts` in document order, inserting structural separators.

    Internal-link content is bracketed with sentinel control characters rather than tracked as offsets,
    because normalisation runs afterwards and would invalidate any offset captured during the walk.
    """
    tag = _localname(element.tag)

    if tag in _SKIP_TAGS or _is_hidden(element):
        # The element is dropped but its tail belongs to the parent's flow, so it is kept.
        if element.tail:
            parts.append(element.tail)
        return

    marked = _is_internal_link(element)
    if marked:
        parts.append(_MARK_OPEN)

    if tag == 'br':
        parts.append('\n')
    if element.text:
        parts.append(element.text)

    for child in element:
        _walk(child, parts)

    if marked:
        parts.append(_MARK_CLOSE)

    if tag in _CELL_TAGS:
        parts.append(' | ')
    elif tag in _LINE_TAGS:
        parts.append('\n')

    if element.tail:
        parts.append(element.tail)


def normalise(text: str) -> str:
    """Collapse whitespace into the canonical form without destroying paragraph structure.

    Invisible characters are deleted outright: they render as nothing, so removing them loses no content while
    making a quoted span match the way a model actually retypes it. Runs of horizontal whitespace collapse to
    one space and runs of blank lines to one, because filings are full of layout padding that would otherwise
    make identical sentences compare unequal. Vertical structure survives as exactly one blank line, which is
    what keeps section boundaries findable later.
    """
    text = _ZERO_WIDTH.sub('', text)
    text = _HORIZONTAL_WS.sub(' ', text)
    text = _EMPTY_CELLS.sub('| ', text)
    text = _TRAILING_WS.sub('\n', text)
    text = _BLANK_RUN.sub('\n\n', text)
    return text.strip()


def _split_marks(marked: str) -> tuple[str, tuple[tuple[int, int], ...]]:
    """Strip the sentinel characters, returning the clean text and the spans they bracketed.

    Unbalanced sentinels are tolerated via a depth counter rather than raising, since one malformed anchor in
    a filing should not cost the whole document.
    """
    pieces = _MARK_SPLIT.split(marked)
    out: list[str] = []
    spans: list[tuple[int, int]] = []
    length = 0
    depth = 0
    opened_at = 0

    for piece in pieces:
        if piece == _MARK_OPEN:
            if depth == 0:
                opened_at = length
            depth += 1
        elif piece == _MARK_CLOSE:
            if depth > 0:
                depth -= 1
                if depth == 0 and length > opened_at:
                    spans.append((opened_at, length))
        elif piece:
            out.append(piece)
            length += len(piece)

    return ''.join(out), tuple(spans)


def clean(html: str | bytes) -> CleanedDocument:
    """Render filing HTML to canonical text plus the offsets of its internal-link content.

    Returns an empty document for empty input rather than raising, since EDGAR occasionally serves a zero-byte
    file and one bad filing should not abort a whole research run.
    """
    if not html:
        return CleanedDocument(text='', link_spans=())

    # lxml refuses a str that declares its own encoding, and bytes let it honour the filing's declared charset.
    source = html.encode('utf-8', errors='replace') if isinstance(html, str) else html
    document = lxml.html.fromstring(source)
    etree.strip_elements(document, etree.Comment, with_tail=False)

    parts: list[str] = []
    _walk(document, parts)

    # Normalise before stripping sentinels: the sentinels are not whitespace, so they survive untouched and
    # the spans they bracket therefore land on final offsets.
    text, spans = _split_marks(normalise(''.join(parts)))
    return CleanedDocument(text=text, link_spans=spans)


def clean_rendered_page(html: str | bytes) -> CleanedDocument:
    """Clean one of EDGAR's rendered statement or note pages, keeping only the statement table itself.

    These pages wrap a small `table.report` in a large tail of XBRL element definitions. Narrowing to that
    table first is far more reliable than trying to trim the tail afterwards, and it turns a 20,000-token
    page into a few hundred tokens of actual financial statement.
    """
    if not html:
        return CleanedDocument(text='', link_spans=())

    source = html.encode('utf-8', errors='replace') if isinstance(html, str) else html
    document = lxml.html.fromstring(source)
    etree.strip_elements(document, etree.Comment, with_tail=False)

    report = document.find('.//table[@class="report"]')
    root = report if report is not None else document

    parts: list[str] = []
    _walk(root, parts)
    text, spans = _split_marks(normalise(''.join(parts)))
    return CleanedDocument(text=text, link_spans=spans)


def to_text(html: str | bytes) -> str:
    """Canonical plain text of `html`, for callers that do not need link spans."""
    return clean(html).text


def estimate_tokens(text: str) -> int:
    """Rough token count. Four characters per token is close enough for budgeting and context checks."""
    return len(text) // 4
