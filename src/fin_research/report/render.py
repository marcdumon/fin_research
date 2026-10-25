"""Render a finished report as markdown, with every claim traceable to the filing it came from.

Citations are numbered inline and listed in full at the end, each giving the document, the character range and
the text that was actually matched. A citation to a reported figure has no character range: it names the
statement line instead, and shows how the claim put it, which is the agent's wording rather than the filing's.
That appendix is the difference between a research note and an opinion: anyone reading it can go to the source
and check, and so can a later run.

The footer states the groundedness rate and how the matches were obtained. It is reported honestly, including
when nothing could be checked -- a note built entirely from judgement gets no score rather than a perfect one,
because a perfect score on nothing is the most misleading number the system could print.
"""

import logging
import re
from dataclasses import dataclass

from fin_research.core.schemas import MatchKind, Report, Valuation
from fin_research.core.verification import ClaimVerdict

log = logging.getLogger(__name__)


@dataclass
class Citations:
    """Numbers each distinct piece of evidence once, in the order it is first cited.

    The evidence numbered here is what the verifier resolved when the claim was made, carried through rather
    than looked up again. Re-resolving at this point would be a second, weaker check -- the renderer has the
    source text but not the passage tables the verifier used -- and the appendix would then describe a
    verification that governed nothing, while the footer claims it governed everything.
    """

    _numbers: dict[str, int] = None  # type: ignore[assignment]
    _entries: list[tuple[int, str, str, str]] = None  # type: ignore[assignment]

    def __post_init__(self) -> None:
        """Start with an empty register."""
        self._numbers = {}
        self._entries = []

    def mark(self, verdict: ClaimVerdict) -> str:
        """Citation markers for one claim, e.g. ' [1][2]', or '' when it has none that resolve."""
        markers = []
        for evidence in verdict.evidence:
            if not evidence.is_grounded:
                continue

            seen = self._numbers.get(evidence.evidence_id)
            if seen is not None:
                markers.append(seen)
                continue

            number = len(self._entries) + 1
            self._numbers[evidence.evidence_id] = number
            location = (f'chars {evidence.char_start}-{evidence.char_end}'
                        if evidence.char_start is not None else 'reported figure')
            self._entries.append((number, evidence.document_id or evidence.fact_id or '-', location,
                                  evidence.matched_text or evidence.quoted_text or ''))
            markers.append(number)

        return ''.join(f'[{n}]' for n in markers)

    def appendix(self) -> str:
        """The numbered list of sources, or a note that there were none."""
        if not self._entries:
            return '_No citations resolved._'
        lines = []
        for number, document, location, text in self._entries:
            quote = ' '.join(text.split())
            quote = quote[:300] + ('...' if len(quote) > 300 else '')
            lines.append(f'{number}. `{document}` ({location}) — "{quote}"')
        return '\n'.join(lines)


# A debater names the checked claims its reasoning rests on, and writes their identifiers into its own text:
# 'Based on [95988326] and [6f95ac32], the turnaround is real'. One or several, in one bracket or many.
REFERENCE = re.compile(r'\[([0-9a-f]{8}(?:\s*,\s*[0-9a-f]{8})*)\]')


def _resolve_references(text: str, citations: Citations, verdicts: dict[str, ClaimVerdict]) -> str:
    """Turn the claim identifiers in a claim's text into the citation numbers of the claims they name.

    An argument that rests on a checked claim inherits that claim's sources, which is the whole reason the
    debate is given identifiers rather than filing text. Left alone the identifiers reach the reader as
    meaningless hexadecimal; resolved, they become citations pointing at the passages behind the reasoning.
    An identifier naming nothing is removed rather than shown -- it cites nothing, so it says nothing.
    """
    def replace(match: re.Match) -> str:
        markers = ''.join(citations.mark(verdicts[identifier])
                          for identifier in (i.strip() for i in match.group(2).split(','))
                          if identifier in verdicts)
        # The space before the bracket belongs to the citation when there is one, and to nothing when the
        # reference resolved to nothing -- otherwise a dropped reference leaves a gap mid-sentence.
        return f'{match.group(1)}{markers}' if markers else ''

    return re.sub(r'(\s*)' + REFERENCE.pattern, replace, text).strip()


def _claims(verdicts: list[ClaimVerdict], citations: Citations,
            known: dict[str, ClaimVerdict] | None = None) -> str:
    """Render a list of claims as bullets, each carrying its citation markers."""
    if not verdicts:
        return '_None supported by the evidence._'
    lines = []
    for verdict in verdicts:
        claim = verdict.claim
        kind = '' if claim.claim_type.value in ('fact', 'numeric') else f' _({claim.claim_type.value})_'
        text = _resolve_references(claim.text, citations, known or {})
        lines.append(f'- {text}{kind}{citations.mark(verdict)}')
    return '\n'.join(lines)


def _valuation(valuation: Valuation | None) -> str:
    """Render the valuation as a scenario table with its assumptions."""
    if valuation is None:
        return '_No valuation was produced._'

    lines = [f'**Method:** {valuation.method}', '']
    if valuation.scenarios:
        lines += ['| Scenario | Value per share | Probability |', '|---|---:|---:|']
        lines += [f'| {s.name} | {s.value_per_share:,.2f} | {s.probability:.0%} |' for s in valuation.scenarios]
        expected = valuation.expected_value
        if expected is not None:
            lines.append(f'| **Expected** | **{expected:,.2f}** | |')
        if valuation.current_price:
            lines += ['', f'Current price: {valuation.current_price:,.2f}']

    if valuation.assumptions:
        lines += ['', '**Assumptions**', '']
        lines += [f'- **{a.name}**: {a.value:,.4g} {a.unit} — {a.rationale}' for a in valuation.assumptions]

    return '\n'.join(lines)


def render(report: Report, verdicts: dict[str, ClaimVerdict], groundedness: float | None = None,
           cost_usd: float | None = None, match_kinds: dict[str, int] | None = None,
           numeric: dict[str, int] | None = None, valuation: Valuation | None = None) -> str:
    """Render the whole report.

    Order follows how a note is actually read: the conclusion first, then the numbers, then both sides of the
    argument, then what would make it wrong.

    The report carries claim identifiers, which are resolved here against what earlier stages produced and
    verified. An identifier naming nothing is dropped: it means the synthesiser invented a reference, and a
    fabricated reference is exactly what the citation contract exists to keep out of the note.
    """
    citations = Citations()

    def resolve(identifiers: list[str]) -> list[ClaimVerdict]:
        """Turn claim identifiers into the verified claims they name, dropping any that name nothing."""
        missing = [i for i in identifiers if i not in verdicts]
        if missing:
            log.warning(f'{len(missing)} of {len(identifiers)} claim reference(s) match no verified claim: '
                        + ', '.join(missing))
        return [verdicts[identifier] for identifier in identifiers if identifier in verdicts]

    figures = resolve(report.numbers)
    numbers = _claims(figures, citations, verdicts) if figures \
        else '_No financial findings survived verification._'

    disagreements = '\n\n'.join(
        f'**{d.question}**\n\n- Constructive: {d.bull_position}\n- Sceptical: {d.bear_position}\n\n'
        f'{"_Resolved:_ " + d.resolution if d.resolved and d.resolution else "_Unresolved on the available evidence._"}'
        for d in report.disagreements) or '_None recorded._'

    falsifiers = '\n'.join(f'- {f}' for f in report.falsifiers) or '_None stated._'

    body = f"""# {report.ticker} — research note

**As of {report.as_of}** · **{report.direction}** · confidence: **{report.confidence}**

## Thesis

{report.thesis}

## What the numbers say

{numbers}

## The constructive case

{_claims(resolve(report.bull_case), citations, verdicts)}

## The sceptical case

{_claims(resolve(report.bear_case), citations, verdicts)}

## What would make this wrong

{falsifiers}

## Unresolved disagreements

{disagreements}

## Valuation

{_valuation(valuation)}

## Sources

{citations.appendix()}
"""

    footer = ['---', '']
    if groundedness is not None:
        footer.append(f'Groundedness: **{groundedness:.0%}** of checkable claims had citations that resolved '
                      'against the filing text.')
    else:
        footer.append('Groundedness: **not measured** — no claim in this note was mechanically checkable.')
    if match_kinds:
        ordered = [k.value for k in MatchKind if k.value in match_kinds]
        footer.append('Match quality: ' + ', '.join(f'{k} {match_kinds[k]}' for k in ordered) + '.')
    if numeric:
        confirmed = numeric.get('confirmed', 0)
        contradicted = numeric.get('contradicted', 0)
        footer.append(f'Figures checked against the filed statements: {confirmed} agreed'
                      + (f', {contradicted} did not and were dropped' if contradicted else '') + '.')
    if cost_usd is not None:
        footer.append(f'Cost: **${cost_usd:.4f}**.')
    footer.append('')
    footer.append('_Citations are verified mechanically: a quote that could not be found in the source was '
                  'discarded along with the claim it supported. Verification confirms that a source says '
                  'what is claimed, not that the claim is correct._')

    return body + '\n' + '\n'.join(footer)
