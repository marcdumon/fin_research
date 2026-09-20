"""Material events: 8-K filings and the press releases attached to them.

This is the 'news' half of the research question, and for fundamental work it is the better half. An 8-K is
primary source, precisely dated, filed under a numbered item that says what kind of event it is, and citable
to the same standard as a 10-K. A newswire summary of the same event is none of those things. Third-party
feeds are deliberately not used here: they need a vendor, they carry no stable identifier to cite, and their
content is mostly a restatement of what the 8-K already says.

The substance usually is not in the 8-K itself, which is often a single paragraph, but in Exhibit 99.1 -- the
earnings release with the actual numbers and management's framing. Exhibit types are not in the filing's JSON
index, so they are read from the SGML header file, which is authoritative and about six kilobytes.
"""

import html
import logging
import re
from dataclasses import dataclass

from fin_research.ingest.documents import fetch_document
from fin_research.ingest.edgar import EVENT_FORMS, EdgarClient, Filing
from fin_research.ingest.text import CleanedDocument, clean

log = logging.getLogger(__name__)

# Form 8-K item numbers. The description is what the filer is legally asserting the event to be, which makes
# it a far better classifier than anything a model would infer from the prose.
ITEM_CODES = {
    '1.01': 'Entry into a material definitive agreement',
    '1.02': 'Termination of a material definitive agreement',
    '1.03': 'Bankruptcy or receivership',
    '1.04': 'Mine safety reporting',
    '1.05': 'Material cybersecurity incident',
    '2.01': 'Completion of acquisition or disposition of assets',
    '2.02': 'Results of operations and financial condition',
    '2.03': 'Creation of a direct financial obligation',
    '2.04': 'Triggering event accelerating a financial obligation',
    '2.05': 'Costs associated with exit or disposal activities',
    '2.06': 'Material impairment',
    '3.01': 'Notice of delisting or failure to satisfy a listing rule',
    '3.02': 'Unregistered sale of equity securities',
    '3.03': 'Material modification to rights of security holders',
    '4.01': "Change in the registrant's certifying accountant",
    '4.02': 'Non-reliance on previously issued financial statements',
    '5.01': 'Change in control of registrant',
    '5.02': 'Departure or election of directors or officers',
    '5.03': 'Amendment to articles of incorporation or bylaws',
    '5.04': 'Temporary suspension of trading under employee benefit plans',
    '5.05': 'Amendment to the code of ethics',
    '5.06': 'Change in shell company status',
    '5.07': 'Submission of matters to a vote of security holders',
    '5.08': 'Shareholder director nominations',
    '7.01': 'Regulation FD disclosure',
    '8.01': 'Other events',
    '9.01': 'Financial statements and exhibits',
}

# Items that change what a security is worth. 9.01 is excluded because it only says an exhibit is attached,
# and 8.01 because filers use it for everything from a buyback to a change of address.
MATERIAL_ITEMS = frozenset({
    '1.01', '1.02', '1.03', '1.05', '2.01', '2.02', '2.04', '2.05', '2.06',
    '3.01', '4.01', '4.02', '5.01', '5.02',
})

# Exhibit types carrying a press release, most specific first.
PRESS_RELEASE_TYPES = ('EX-99.1', 'EX-99', 'EX-99.2')

_DOCUMENT_BLOCK = re.compile(
    r'<DOCUMENT>\s*<TYPE>(?P<type>[^\s<]+)\s*(?:<SEQUENCE>(?P<sequence>\d+)\s*)?'
    r'<FILENAME>(?P<filename>[^\s<]+)\s*(?:<DESCRIPTION>(?P<description>[^\n<]*))?',
    re.IGNORECASE)


@dataclass(frozen=True)
class Exhibit:
    """One document attached to a filing, as the SGML header describes it."""

    type: str
    filename: str
    sequence: int | None = None
    description: str = ''

    @property
    def is_press_release(self) -> bool:
        """True for the exhibit types companies file earnings releases under."""
        return self.type.upper() in PRESS_RELEASE_TYPES


@dataclass(frozen=True)
class Event:
    """An 8-K, with the numbered items the filer asserted it to be about."""

    filing: Filing
    items: tuple[str, ...]

    @property
    def descriptions(self) -> tuple[str, ...]:
        """Human-readable description of each item, skipping codes SEC has not defined."""
        return tuple(ITEM_CODES[code] for code in self.items if code in ITEM_CODES)

    @property
    def is_material(self) -> bool:
        """True if any item is one that moves a valuation."""
        return bool(set(self.items) & MATERIAL_ITEMS)

    @property
    def is_earnings(self) -> bool:
        """True for the results announcement, which is the event most worth reading."""
        return '2.02' in self.items


def parse_items(raw: str) -> tuple[str, ...]:
    """Split the submissions index `items` field into item codes.

    SEC returns them comma-separated and occasionally with trailing prose, so codes are matched rather than
    split blindly.
    """
    return tuple(dict.fromkeys(re.findall(r'\d\.\d{2}', raw or '')))


async def events(client: EdgarClient, cik: int, *, material_only: bool = False, **kwargs) -> list[Event]:
    """Material-event filings for a company, newest first."""
    filings = await client.filings(cik, forms=EVENT_FORMS, **kwargs)
    found = [Event(filing=f, items=parse_items(f.items)) for f in filings]
    return [e for e in found if e.is_material] if material_only else found


async def exhibits(client: EdgarClient, filing: Filing) -> list[Exhibit]:
    """Every document in a filing, with the exhibit type the filer declared.

    Read from the SGML header rather than the JSON index, which lists filenames without types -- and a press
    release is identified by being EX-99.1, not by its filename, which filers compose freely.

    The header is served inside an HTML page with its SGML tags entity-escaped, so it is unescaped before
    matching; without that the document blocks are invisible to the pattern.
    """
    name = f'{filing.accession}-index-headers.html'
    try:
        raw = await fetch_document(client, filing, name)
    except Exception as error:
        log.warning(f'no header index for {filing.accession}: {type(error).__name__}: {error}')
        return []

    found = []
    for match in _DOCUMENT_BLOCK.finditer(html.unescape(raw)):
        sequence = match.group('sequence')
        found.append(Exhibit(
            type=match.group('type').strip(),
            filename=match.group('filename').strip(),
            sequence=int(sequence) if sequence else None,
            description=(match.group('description') or '').strip()))
    return found


async def press_release(client: EdgarClient, filing: Filing) -> CleanedDocument | None:
    """The press release attached to an 8-K, cleaned, or None if the filing has no such exhibit.

    Returns the most specific exhibit type available, because a company that files both EX-99.1 and EX-99.2
    puts the earnings release in the first and supplementary material in the second.
    """
    attached = await exhibits(client, filing)
    for wanted in PRESS_RELEASE_TYPES:
        for exhibit in attached:
            if exhibit.type.upper() == wanted:
                return clean(await fetch_document(client, filing, exhibit.filename))
    return None
