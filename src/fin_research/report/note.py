"""One assembled view of a finished run, which the markdown renderer and the dashboard both read from.

The report a run produces carries claim identifiers, not claims. Resolving them needs the run's other
artifacts and the verification record kept alongside them, and doing that twice -- once to write a file and
again to answer an HTTP request -- is how a file on disk and a page in a browser come to disagree about what
a company's research says. Both go through here instead.

Nothing in this module calls a model or reads a filing. It reassembles what a run already decided.
"""

import logging
import re
from dataclasses import dataclass, field
from datetime import date

from fin_research.core.schemas import (
    Claim,
    Disagreement,
    Evidence,
    EvidenceKind,
    Finding,
    FindingSet,
    MatchKind,
    Report,
    Thesis,
    Valuation,
)
from fin_research.core.state import State

log = logging.getLogger(__name__)

# A debater names the checked claims its reasoning rests on by writing their identifiers into its own
# text: 'Based on [95988326] and [6f95ac32], the turnaround is real'. The synthesiser does the same when it
# summarises a disagreement, but writes them in parentheses instead, so both are accepted. Either way they
# are identifiers, and a reader shown eight hexadecimal characters has been shown nothing.
REFERENCE = re.compile(r'(\s*)[\[(]([0-9a-f]{8}(?:\s*,\s*[0-9a-f]{8})*)[\])]')


@dataclass(frozen=True)
class Cited:
    """One claim as it appears in the note, with the evidence that was accepted for it."""

    claim: Claim
    evidence: tuple[Evidence, ...]
    grounded: bool
    reason: str

    @property
    def match(self) -> MatchKind | None:
        """The weakest rung any of this claim's evidence needed, which is how far it can be trusted."""
        spans = [item.match_kind for item in self.evidence if item.kind is EvidenceKind.SPAN]
        return max(spans, key=lambda k: [m.value for m in MatchKind].index(k.value)) if spans else None


@dataclass
class NoteView:
    """A finished run, assembled: the report with every identifier resolved to the claim it names."""

    run_id: str
    ticker: str
    report: Report
    claims: dict[str, Cited] = field(default_factory=dict)
    valuation: Valuation | None = None
    theses: list[Thesis] = field(default_factory=list)
    groundedness: float | None = None
    match_kinds: dict[str, int] = field(default_factory=dict)
    numeric: dict[str, int] = field(default_factory=dict)
    checked: int | None = None
    dropped: int | None = None
    cost_usd: float | None = None

    def resolve(self, identifiers: list[str]) -> list[Cited]:
        """Turn claim identifiers into the claims they name, dropping any that name nothing."""
        missing = [i for i in identifiers if i not in self.claims]
        if missing:
            log.warning(f'{len(missing)} of {len(identifiers)} claim reference(s) match no verified claim: '
                        + ', '.join(missing))
        return [self.claims[i] for i in identifiers if i in self.claims]

    @property
    def numbers(self) -> list[Cited]:
        """The claims about the reported figures."""
        return self.resolve(self.report.numbers)

    @property
    def bull(self) -> list[Cited]:
        """The claims the constructive case rests on."""
        return self.resolve(self.report.bull_case)

    @property
    def bear(self) -> list[Cited]:
        """The claims the sceptical case rests on."""
        return self.resolve(self.report.bear_case)

    @property
    def disagreements(self) -> list[Disagreement]:
        """What the two sides could not settle."""
        return list(self.report.disagreements)


def _verdicts_from_artifact(payload: dict) -> dict[str, Cited]:
    """Rebuild the verification record a run stored when it finished."""
    found: dict[str, Cited] = {}
    for claim_id, entry in payload.items():
        found[claim_id] = Cited(
            claim=Claim.model_validate(entry['claim']),
            evidence=tuple(Evidence.model_validate(item) for item in entry.get('evidence', [])),
            grounded=bool(entry.get('grounded', True)),
            reason=str(entry.get('reason', '')))
    return found


def modernise(payload: dict) -> tuple[dict, list[Claim], dict | None]:
    """Rewrite a report stored before the schema settled, returning it with what it used to carry inside it.

    The report used to hold whole claims, and the synthesiser wrote them out again with citations it could
    not have, which is why it now holds identifiers instead. Earlier still, it embedded the whole valuation,
    which made the schema deep enough that a provider refused the request. Reports written before either
    change are on disk and worth reading, so what they carried is lifted out and the note is assembled from
    it as though nothing had changed. `extra=forbid` is what makes this necessary and also what makes it
    safe: a field the schema no longer knows is an old field, not a silent one.
    """
    from fin_research.agents.base import identify

    lifted: list[Claim] = []
    updated = {k: v for k, v in payload.items() if k in Report.model_fields}
    embedded_valuation = payload.get('valuation') if isinstance(payload.get('valuation'), dict) else None
    for field_name in ('numbers', 'bull_case', 'bear_case'):
        entries = payload.get(field_name) or []
        if not any(isinstance(entry, dict) for entry in entries):
            continue
        identifiers = []
        for entry in entries:
            if not isinstance(entry, dict):
                identifiers.append(entry)
                continue
            claim = identify(Claim.model_validate(entry))
            lifted.append(claim)
            identifiers.append(claim.claim_id)
        updated[field_name] = identifiers
    return updated, lifted, embedded_valuation


def reconstruct_verdicts(con, ticker: str, as_of: date, findings: list[Finding],
                         theses: list[Thesis], extra: list[Claim] | None = None) -> dict[str, Cited]:
    """Check a run's claims again, for a run that finished before the record was kept.

    A fallback, and honest about being one: it compares the claims against the filings as they stand now
    rather than as they stood during the run, so a company re-ingested since may have citations that no
    longer resolve. Better than showing a note with no sources under it, and every run from here on stores
    its own record and never comes through this path.
    """
    from fin_research.agents.base import identify
    from fin_research.core.passages import PassageIndex
    from fin_research.core.supervisor import load_facts, load_sources
    from fin_research.core.verification import SourceText, verify_claims

    sources_for = load_sources(con, ticker, as_of)
    facts = load_facts(con, ticker, as_of)

    sources, passages = {}, {}
    named = list(sources_for.sections.values())
    named += [s for s in (sources_for.statements, sources_for.events) if s]
    for source_id, text in named:
        sources[source_id] = SourceText(text)
        passages[source_id] = PassageIndex.build(source_id, text)

    # A run from before statements had an identifier of their own cited them under the first page's,
    # with offsets measured in the joined text. The same text is registered under that name as well, or every
    # figure such a run cited from the statements would stop resolving.
    if sources_for.statements is not None:
        from fin_research.ingest.store import STATEMENTS_PREFIX, first_statements_page

        statements_id, text = sources_for.statements
        legacy = first_statements_page(con, statements_id.removeprefix(STATEMENTS_PREFIX))
        if legacy:
            sources[legacy] = sources[statements_id]
            passages[legacy] = PassageIndex.build(legacy, text)

    found: dict[str, Cited] = {}
    batches = [finding.claims for finding in findings] + [thesis.claims for thesis in theses]
    if extra:
        batches.append(extra)
    for claims in batches:
        for verdict in verify_claims(claims, sources, facts, passages).verdicts:
            identifier = identify(verdict.claim).claim_id
            found.setdefault(identifier, Cited(claim=verdict.claim, evidence=verdict.evidence,
                                               grounded=verdict.grounded, reason=verdict.reason))
    return found


def build_note(store: State, run_id: str, con=None) -> NoteView | None:
    """Assemble a run's note from what it stored, or None when it produced no report.

    Reads the stored verification record rather than checking the citations again. Re-checking would compare
    a finished run's claims against whatever the filings look like today, which is a different question from
    the one the run answered. A run from before that record was kept falls back to checking again, when a
    connection to the filings is given.
    """
    artifacts = store.load_artifacts(run_id)
    report: Report | None = None
    valuation: Valuation | None = None
    theses: list[Thesis] = []
    findings: list[Finding] = []
    embedded: list[Claim] = []
    claims: dict[str, Cited] = {}
    recorded: dict = {}

    for artifact in artifacts:
        kind, payload = artifact['artifact_type'], artifact['payload']
        if kind == 'Report':
            payload, lifted, older_valuation = modernise(payload)
            embedded.extend(lifted)
            report = Report.model_validate(payload)
            if older_valuation and valuation is None:
                valuation = Valuation.model_validate(older_valuation)
        elif kind == 'Valuation':
            valuation = Valuation.model_validate(payload)
        elif kind == 'Thesis':
            theses.append(Thesis.model_validate(payload))
        elif kind == 'FindingSet':
            findings.extend(FindingSet.model_validate(payload).findings)
        elif kind == 'Verdicts':
            claims = _verdicts_from_artifact(payload)
        elif kind == 'VerificationSummary':
            recorded = payload

    if report is None:
        return None

    if not claims and con is not None:
        claims = reconstruct_verdicts(con, report.ticker, report.as_of, findings, theses, embedded)

    # The verification figures come from the run, never from the claims kept here. Everything kept here
    # survived checking, so a share computed over it is 100% by construction: the page said every claim was
    # verified while the note on disk, computed during the run over everything including what was dropped,
    # said 79%. A run from before the figures were recorded has none, and says so rather than inventing one.
    summary = store.run(run_id)
    return NoteView(
        run_id=run_id,
        ticker=report.ticker,
        report=report,
        claims=claims,
        valuation=valuation,
        theses=theses,
        groundedness=recorded.get('groundedness'),
        match_kinds=recorded.get('match_kinds', {}),
        numeric=recorded.get('numeric', {}),
        checked=recorded.get('checked'),
        dropped=recorded.get('dropped'),
        cost_usd=summary.spend_usd if summary else None)


def as_json(note: NoteView) -> dict:
    """The note as the dashboard reads it: every claim resolved, every citation numbered once.

    Numbering happens here rather than in the browser so the markdown file and the page agree on which
    source is `[3]`, and so a claim's inline references to other claims can be rewritten into the same
    numbering before anything renders them.
    """
    numbers: dict[str, int] = {}
    sources: list[dict] = []

    def register(cited: Cited) -> list[int]:
        """Number this claim's evidence, reusing a number already given to the same span."""
        marks = []
        for item in cited.evidence:
            if not item.is_grounded:
                continue
            seen = numbers.get(item.evidence_id)
            if seen is None:
                seen = len(sources) + 1
                numbers[item.evidence_id] = seen
                sources.append({
                    'n': seen,
                    'kind': item.kind.value,
                    'source_id': item.document_id or item.fact_id or '',
                    'char_start': item.char_start,
                    'char_end': item.char_end,
                    'match_kind': item.match_kind.value,
                    'text': ' '.join((item.matched_text or item.quoted_text or '').split()),
                })
            marks.append(seen)
        return marks

    def replace(match: re.Match) -> str:
        """Turn a reference to another claim into that claim's citation numbers, or remove it."""
        inner = [i.strip() for i in match.group(2).split(',')]
        referenced = [n for i in inner if i in note.claims for n in register(note.claims[i])]
        return f'{match.group(1)}{"".join(f"[{n}]" for n in referenced)}' if referenced else ''

    def rewrite(text: str) -> str:
        """Resolve every claim reference in a passage of the note's prose."""
        return REFERENCE.sub(replace, text).strip()

    def render_claim(cited: Cited) -> dict:
        """One claim, with its own citations and any references to other claims turned into citations."""
        marks = register(cited)
        return {
            'claim_id': cited.claim.claim_id,
            'text': rewrite(cited.claim.text),
            'claim_type': cited.claim.claim_type.value,
            'polarity': cited.claim.polarity.value,
            'metric': cited.claim.metric,
            'match_kind': cited.match.value if cited.match else None,
            'citations': marks,
        }

    body = {
        'run_id': note.run_id,
        'ticker': note.ticker,
        'as_of': str(note.report.as_of),
        'thesis': rewrite(note.report.thesis),
        'direction': note.report.direction,
        'confidence': note.report.confidence,
        'numbers': [render_claim(c) for c in note.numbers],
        'bull_case': [render_claim(c) for c in note.bull],
        'bear_case': [render_claim(c) for c in note.bear],
        'falsifiers': list(note.report.falsifiers),
        # The argument, as each side put it. The report carries the conclusion; this is the reasoning
        # that produced it, which is the part a reader disagrees with.
        'theses': [{'stance': thesis.stance.value, 'round': thesis.round,
                    'summary': rewrite(thesis.summary),
                    'falsifiers': list(thesis.falsifiers),
                    'claims': [rewrite(c.text) for c in thesis.claims]}
                   for thesis in note.theses],
        'disagreements': [{**d.model_dump(mode='json'),
                           'bull_position': rewrite(d.bull_position),
                           'bear_position': rewrite(d.bear_position),
                           'resolution': rewrite(d.resolution) if d.resolution else None}
                          for d in note.disagreements],
        'valuation': note.valuation.model_dump(mode='json') if note.valuation else None,
        'expected_value': note.valuation.expected_value if note.valuation else None,
        'groundedness': note.groundedness,
        'checked': note.checked,
        'dropped': note.dropped,
        'numeric': note.numeric,
        'match_kinds': note.match_kinds,
        'cost_usd': note.cost_usd,
    }
    body['sources'] = sources
    return body


def latest_note(store: State, ticker: str, con=None) -> NoteView | None:
    """The most recent completed run's note for a company."""
    rows = store.query(
        "SELECT run_id FROM runs WHERE upper(ticker) = ? AND status = 'done' ORDER BY started_at DESC",
        (ticker.upper(),))
    for row in rows:
        note = build_note(store, row['run_id'], con)
        if note is not None:
            return note
    return None
