# fin_research

An agent swarm that reads SEC filings and writes equity research, where every factual claim is traceable to a
quotation that has been mechanically verified against the source.

Most of the work is done by cheap models. That is possible because of one rule: **an agent's model tier
follows how checkable its output is, not how hard its task feels.** Pulling risk factors out of a 10-K feels
harder than writing an investment thesis, but each of its claims must quote the filing and each quote is
checked by code, so a cheap model's mistakes are caught. Writing a thesis is easy to do badly and impossible
to check, so that goes to an expensive model. Reading a full annual report costs about **$0.02** on the cheap
tier against **$0.13** on the expensive one — prices and context windows verified against
`openrouter.ai/api/v1/models`, and carried in `fin_research.toml` so they can be corrected as they move.

## What it does

```
thousands of companies
   |  screen: numbers only, no filings read, free
tens
   |  triage: one cheap call each, reads Item 1, most get rejected
a handful
   |  deep dive: nine agents, a debate, a valuation
one research note
```

Each stage costs more per company than the one before and removes most of what reaches it, which is the only
reason deep research across a broad universe is affordable. Screening a 2,200-company universe takes under a
second and reads nothing; triage costs about a third of a cent per company; the deep dive costs real money
and runs only on what survived both.

Running alongside it, a **monitor** patrols a watchlist and says where to point that machinery next. Every
check it makes is mechanical -- a new filing, an 8-K item number, a price move, a screen just entered,
statements that stopped adding up -- so a patrol costs nothing and can run on a schedule. Detection is cheap;
interpretation is expensive; the two are never swapped.

Within a deep dive:

```
ticker -> filings -> sections -> evidence -> findings -> debate -> report
                  \-> XBRL facts -> reconciled statements -/
```

Nine agents across six stages. Four read the filing in parallel, one reads the reconciled statements, then a
bull and a bear argue from the same evidence for exactly three turns, a valuation is produced, and a
synthesiser writes the note and rules on what the debate left unsettled.

Agents never talk to each other. They emit typed artifacts and a supervisor routes them. The single exception
is the debate, which is where genuine disagreement is worth paying for — and it is fixed at three calls, with
no convergence loop.

## The guarantees

- **A fabricated citation cannot reach the report.** Agents quote; they never supply offsets, because models
  cannot count characters. The verifier locates each quote in the canonical text and assigns the offsets
  itself. A quote that is not found is discarded along with the claim it supported.
- **Numbers are checked against the filer's own arithmetic, twice over.** Every stated figure is compared
  against what the company actually reported, so a model that quotes a real sentence and states the wrong
  revenue has the claim dropped — span checking alone passes that, which is why it is not enough. Separately,
  the statements must reconcile: assets equal liabilities plus equity, and the cash flow sections plus
  currency effects equal the change in cash. Both checks are three-valued — pass, fail, or skipped naming
  what was missing — because a check that silently skips reports a success it did not earn.
- **Spend is refused, not just reported.** The budget is checked before a request is sent. Every call is
  recorded, including failures.
- **A human can stand in front of the expensive steps.** `--gate expensive` makes the run stop and wait for a
  decision in the dashboard before each costly call. The pending request is a row in the database, so a run
  interrupted while waiting can be resumed rather than stranded. Off by default: a gated run blocks until
  someone answers, which would silently stall an unattended job.
- **Point in time.** Every document and fact carries its filing date, so a run dated 2023 reads only what was
  public in 2023. Screening enforces the same rule against the market data, which required discarding rows the
  source claims were published before the period they cover, and reading restatements from their restated date
  rather than the original filing's.

## Use

```bash
uv sync
cp .env.example .env          # add OPENROUTER_API_KEY

# Fetch and normalise. No model involved, so this is free and cacheable.
uv run python -m fin_research.ingest init
uv run python -m fin_research.ingest ingest --ticker AAPL --years 5 --statements --news
uv run python -m fin_research.ingest verify --ticker AAPL      # do its statements reconcile?
uv run python -m fin_research.ingest score --tickers AAPL JPM  # regression gate, no model needed

# Screen a universe. Free, reads no filings, and honest about any past date.
uv run python -m fin_research.screen list
uv run python -m fin_research.screen run --screen quality_value
uv run python -m fin_research.screen run --screen quality_value --as-of 2020-09-01

# Screen, then read the survivors and decide which earn a deep dive. Costs a few cents.
uv run python -m fin_research.screen funnel --screen quality_value --limit 20 --budget 1.00

# Patrol a watchlist. No model involved, so run it as often as you like.
uv run python -m fin_research.monitor watch --tickers AAPL KO NVDA
uv run python -m fin_research.monitor patrol
uv run python -m fin_research.monitor alerts

# Research. This is the part that costs money.
uv run python -m fin_research.agents roster
uv run python -m fin_research.agents run --ticker AAPL --budget 2.00
uv run python -m fin_research.agents run --ticker AAPL --budget 2.00 --gate expensive
uv run python -m fin_research.agents cost

# Watch it work, start runs, answer the gates, and read what the monitor found.
uv run uvicorn fin_research.web.api:app --port 8000   # http://127.0.0.1:8000
```

## Layout

| Package | What it does |
|---|---|
| `ingest/` | EDGAR client, HTML to canonical text, 10-K section splitting, XBRL normalisation, 8-K events, DuckDB store |
| `core/` | typed artifacts, citation verification, SQLite run state, the supervisor, evaluation |
| `llm/` | the single `call_model` path, tier routing, structured-output parsing |
| `agents/` | the nine agents and their prompts |
| `report/` | markdown rendering with a numbered source appendix |
| `screen/` | point-in-time factor screening over the attached market data, and the funnel |
| `monitor/` | watchlist, mechanical change detection, and the alert ledger |
| `web/` | FastAPI reading the run ledger, streaming it as server-sent events |

Prices and fundamentals are not duplicated here: the `investalyze` database is attached read-only.

## Where it stands

Verified on real filings: section splitting on **72 filings** across 6 filers and 12 years with no failures;
accounting identities at **363 of 364** checks across 22 filers. **229 tests**, none of which touch a network
or a provider.

**Nothing here has yet made a real model call.** The verification, budgeting and parsing are exercised against
recorded fakes, which tests this code against a model of how providers behave. The first real run is a
discovery step, not a confirmation one.

Known limits, all flagged in code rather than hidden:

- **Banks and insurers are out of scope**, by decision rather than oversight. The ratios this system screens
  on do not describe them -- leverage is the business for a bank -- and they satisfy Item 7 with a pointer to
  a page range rather than content under the heading. The shipped screens exclude Financial Services, and a
  bank that is researched directly will produce a note without a management discussion, flagged as such.
- **10-Q is refused.** Quarterly filings number items separately per part, so ordering cannot disambiguate
  them; the splitter declines rather than guessing.
- **Reorganised tickers raise.** One major oil company's ticker currently resolves to a registrant with no
  annual reports at all; pass the predecessor CIK explicitly.
- **A large bank's filing exceeds a cheap model's context** at roughly 290k tokens. Sections are truncated
  with a marker rather than silently.
- Verification confirms that a source says what is claimed. It does not confirm the claim is correct.
