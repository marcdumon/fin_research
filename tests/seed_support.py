"""Shared helpers for the demo scripts: real filing sections plus replies that quote them.

Lives beside the tests because it reuses their recorded replies, and keeping one copy means the demo cannot
drift from what the tests actually exercise.
"""

from conftest import filing_html
from test_end_to_end import _finding_set, _quote, _report, _thesis, _valuation

from fin_research.core.supervisor import Sources
from fin_research.ingest.sections import split
from fin_research.ingest.text import clean


def sources_for(fixture: str) -> Sources:
    """Real sections of a stored filing, shaped as the supervisor expects them."""
    document = clean(filing_html(fixture))
    result = split(document)
    items = {i: result.by_item(i).text(document.text)
             for i in ('1', '1A', '7') if result.by_item(i) and not result.by_item(i).is_redirect}
    return Sources(sections={k: (f'doc-{k}', v) for k, v in items.items()},
                   statements=('doc-fin', next(iter(items.values()))[:5_000]))


def replies_for(sources: Sources) -> dict:
    """Canned agent replies quoting those sections verbatim, so verification does real work."""
    text = {key: value for key, (_, value) in sources.sections.items()}
    first = next(iter(text.values()))
    return {
        'business': _finding_set('doc-1', [_quote(text.get('1', first), 500)]),
        'risk_factors': _finding_set('doc-1A', [_quote(text.get('1A', first), 800)]),
        'management_view': _finding_set('doc-7', [_quote(text.get('7', first), 1200)]),
        'financials': _finding_set('doc-fin', [_quote(first, 2500)]),
        'bull': _thesis('bull'), 'bear': _thesis('bear'), 'valuation': _valuation(),
        'synthesis': _report('doc-1', _quote(first, 500)),
    }
