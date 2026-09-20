"""Tests for the canonical text rendering.

The property that matters most is determinism: every stored citation is an offset into this output, so a
change in rendering silently invalidates stored  evidence. The rest of these tests pin the specific filing
hazards -- invisible characters, table structure, and contents-row links -- that motivated the design.
"""

from conftest import filing_html

from fin_research.ingest.text import _WIDE_SPACE_CHARS, _ZERO_WIDTH_CHARS, CLEANER_VERSION, clean, estimate_tokens, normalise, to_text


def test_empty_input_yields_empty_document():
    assert clean('').text == ''
    assert clean(b'').link_spans == ()


def test_invisible_characters_are_removed():
    """A model retyping a quote produces ASCII, so an invisible character would break a correct citation."""
    assert normalise(f'Risk{_ZERO_WIDTH_CHARS}Factors') == 'RiskFactors'
    assert normalise(f'Risk{_WIDE_SPACE_CHARS}Factors') == 'Risk Factors'


def test_whitespace_collapses_but_paragraphs_survive():
    assert normalise('a   \t  b') == 'a b'
    assert normalise('a\n\n\n\n\nb') == 'a\n\nb'
    assert normalise('  padded  ') == 'padded'


def test_table_cells_keep_their_boundaries():
    """Statement tables are unreadable as number soup; the pipe is what keeps rows legible to a model."""
    text = to_text('<table><tr><td>Revenue</td><td>416,161</td></tr></table>')
    assert 'Revenue' in text and '416,161' in text
    assert '|' in text


def test_script_and_style_are_dropped():
    text = to_text('<div>keep<script>var x = 1;</script><style>.a{}</style></div>')
    assert 'keep' in text
    assert 'var x' not in text and '.a{' not in text


def test_internal_links_are_recorded_external_ones_are_not():
    """A contents row is an internal anchor; an outbound link is ordinary content."""
    doc = clean('<p><a href="#item1">Item 1.</a> <a href="https://x.test">Elsewhere</a></p>')
    linked = [doc.text[a:b] for a, b in doc.link_spans]
    assert linked == ['Item 1.']


def test_str_and_bytes_inputs_agree():
    html = '<html><head><meta charset="utf-8"></head><body><p>Hello</p></body></html>'
    assert to_text(html) == to_text(html.encode('utf-8'))


def test_real_filing_is_deterministic_and_clean():
    raw = filing_html('aapl_tech')
    first, second = clean(raw), clean(raw)
    assert first.text == second.text
    assert first.link_spans == second.link_spans
    assert first.cleaner_version == CLEANER_VERSION
    assert first.link_spans, 'a modern filing has a hyperlinked table of contents'
    assert not any(ch in first.text for ch in _ZERO_WIDTH_CHARS + _WIDE_SPACE_CHARS)


def test_in_link_discriminates_contents_row_from_body_heading():
    """The whole section splitter rests on this distinction."""
    doc = clean(filing_html('aapl_tech'))
    contents = doc.text.find('Item 1A')
    body = doc.text.find('Item 1A', 30_000)
    assert doc.in_link(contents) is True
    assert doc.in_link(body) is False


def test_estimate_tokens_is_proportional():
    assert estimate_tokens('x' * 400) == 100
