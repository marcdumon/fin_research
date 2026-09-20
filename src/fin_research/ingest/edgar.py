"""SEC EDGAR submissions client: ticker -> CIK, and a CIK's full filing history.

Adapted from investalyze's `apps/data_quality/edgar.py`, which solved the two non-obvious parts already: SEC
partitions a CIK's filing history so that `filings.recent` holds only the ~1000 newest and everything older
sits in separate chunk files that must be merged back in, and the whole API is rate-limited to 10 req/s.

Two deliberate departures from that original. It is async, because the layer above fans out over dozens of
documents at once and threads would mean retrofitting later. And throttling is a shared token gate rather than
a bare `time.sleep`, because under concurrency N coroutines sleeping independently still issue N simultaneous
requests -- the sleep paces each caller, not the endpoint.
"""

import asyncio
import json
import logging
import time
from dataclasses import dataclass
from datetime import date
from pathlib import Path

import httpx

from fin_research.config import Config, read

log = logging.getLogger(__name__)

SUBMISSIONS_URL = 'https://data.sec.gov/submissions/CIK{cik:010d}.json'
CHUNK_URL = 'https://data.sec.gov/submissions/{filename}'
TICKERS_URL = 'https://www.sec.gov/files/company_tickers.json'
ARCHIVE_URL = 'https://www.sec.gov/Archives/edgar/data/{cik}/{accession_nodash}'

PERIODIC_FORMS = frozenset({'10-K', '10-K/A', '10-Q', '10-Q/A'})
ANNUAL_FORMS = frozenset({'10-K', '10-K/A', '10-K405', '10-KSB'})

# Amendments restate only the parts being corrected, so one often contains none of the narrative a reader
# wants. Prefer an original when choosing a single filing to read.
ORIGINAL_ANNUAL_FORMS = frozenset({'10-K', '10-K405', '10-KSB'})
EVENT_FORMS = frozenset({'8-K', '8-K/A'})


class TickerNotFound(LookupError):
    """SEC lists no CIK for the ticker."""


class NoFilingHistory(LookupError):
    """The ticker resolves to a CIK that has filed no annual reports.

    This is not hypothetical. After a holding-company reorganisation SEC's ticker file points at the surviving
    registrant, whose own filing history starts at the reorganisation -- ExxonMobil's XOM currently resolves to
    a CIK carrying registration statements and no 10-K at all, while decades of annual reports sit under the
    predecessor. Returning an empty list there would let a research run produce a confident report built on no
    filings, so the caller is made to supply the intended CIK instead.
    """


@dataclass(frozen=True)
class Filing:
    """One filing as EDGAR reports it.

    `filed` is carried separately from `report_date` and is never dropped: a metric's period end says what it
    describes, but only the filing date says when the market could first have known it. Without it, evaluating
    the swarm's own past calls would silently look ahead.
    """

    cik: int
    accession: str
    form: str
    filed: date
    report_date: date | None
    primary_document: str
    items: str = ''

    @property
    def accession_nodash(self) -> str:
        """Accession number without dashes, as the Archives path spells it."""
        return self.accession.replace('-', '')

    @property
    def base_url(self) -> str:
        """Archive directory holding every document in this filing."""
        return ARCHIVE_URL.format(cik=self.cik, accession_nodash=self.accession_nodash)

    @property
    def primary_url(self) -> str:
        """Direct URL of the filing's primary document."""
        return f'{self.base_url}/{self.primary_document}'

    @property
    def filing_summary_url(self) -> str:
        """URL of FilingSummary.xml, which names every rendered statement and note page."""
        return f'{self.base_url}/FilingSummary.xml'


class _Throttle:
    """Gate that lets at most one request through per `interval` seconds, process-wide.

    A lock plus a monotonic next-slot stamp rather than a semaphore: the constraint is a rate, not a
    concurrency level, so each caller must reserve its slot before sleeping toward it.
    """

    def __init__(self, interval: float) -> None:
        self._interval = interval
        self._lock = asyncio.Lock()
        self._next_slot = 0.0

    async def acquire(self) -> None:
        """Block until this caller's turn, then reserve the following slot."""
        async with self._lock:
            now = time.monotonic()
            wait = self._next_slot - now
            if wait > 0:
                await asyncio.sleep(wait)
            self._next_slot = max(now, self._next_slot) + self._interval


def _parse_date(text: str | None) -> date | None:
    """Parse an ISO date string, or None on empty or malformed input."""
    if not text:
        return None
    try:
        return date.fromisoformat(text)
    except ValueError:
        return None


class EdgarClient:
    """Rate-limited, disk-cached async access to the SEC submissions API.

    Owns its cache policy so no caller has to think about it: a CIK's filing history changes at most a few
    times a quarter, so a multi-day TTL turns repeated research runs over the same names into local reads.
    """

    def __init__(self, cfg: Config | None = None, client: httpx.AsyncClient | None = None) -> None:
        self.cfg = cfg or read()
        self._client = client
        self._owns_client = client is None
        self._throttle = _Throttle(self.cfg.edgar.rate_limit_seconds)
        self._cache_dir = self.cfg.data_root / 'sec' / 'submissions'
        self._ticker_map: dict[str, int] | None = None

    async def __aenter__(self) -> 'EdgarClient':
        """Open the underlying HTTP client if this instance owns one."""
        if self._client is None:
            self._client = httpx.AsyncClient(headers=self.cfg.edgar.headers, timeout=30.0, follow_redirects=True)
        return self

    async def __aexit__(self, *exc: object) -> None:
        """Close the HTTP client, but only if this instance created it."""
        if self._owns_client and self._client is not None:
            await self._client.aclose()
            self._client = None

    @property
    def client(self) -> httpx.AsyncClient:
        """The live HTTP client, or a clear error if used outside the context manager."""
        if self._client is None:
            raise RuntimeError('EdgarClient must be used as an async context manager')
        return self._client

    def _is_fresh(self, path: Path) -> bool:
        """True if `path` exists and is younger than the configured cache TTL."""
        if not path.exists():
            return False
        age_days = (time.time() - path.stat().st_mtime) / 86400
        return age_days < self.cfg.edgar.cache_ttl_days

    async def get_text(self, url: str, cache_path: Path | None = None) -> str:
        """Fetch `url` as text, serving from `cache_path` while it is fresh and writing it back when not."""
        if cache_path is not None and self._is_fresh(cache_path):
            return cache_path.read_text()

        await self._throttle.acquire()
        log.info(f'GET {url}')
        response = await self.client.get(url, headers=self.cfg.edgar.headers)
        response.raise_for_status()

        if cache_path is not None:
            cache_path.parent.mkdir(parents=True, exist_ok=True)
            cache_path.write_text(response.text)
        return response.text

    async def get_json(self, url: str, cache_path: Path | None = None) -> dict:
        """Fetch `url` and parse it as JSON, with the same cache policy as `get_text`."""
        return json.loads(await self.get_text(url, cache_path))

    async def ticker_map(self) -> dict[str, int]:
        """Uppercase ticker to CIK for every SEC-registered company, memoised per instance."""
        if self._ticker_map is None:
            raw = await self.get_json(TICKERS_URL, self._cache_dir / 'company_tickers.json')
            self._ticker_map = {row['ticker'].upper(): int(row['cik_str']) for row in raw.values()}
        return self._ticker_map

    async def resolve_cik(self, ticker: str) -> int | None:
        """CIK for `ticker`, or None if SEC does not list it."""
        return (await self.ticker_map()).get(ticker.upper())

    async def resolve_filer(self, ticker: str) -> int:
        """CIK for `ticker`, verified to have annual reports behind it.

        Raises rather than returning an empty history, because a deep dive that silently reads no filings is
        the exact failure the rest of this package is built to make impossible.
        """
        cik = await self.resolve_cik(ticker)
        if cik is None:
            raise TickerNotFound(f'SEC lists no CIK for {ticker!r}')

        if not await self.filings(cik, forms=ANNUAL_FORMS, limit=1):
            raise NoFilingHistory(
                f'{ticker!r} resolves to CIK {cik}, which has filed no annual report. This usually means a '
                f'reorganisation moved the ticker to a new registrant; pass the predecessor CIK explicitly.')
        return cik

    async def submissions(self, cik: int, *, since: date | None = None, full_history: bool = False) -> dict:
        """Submissions JSON for `cik`, merging older chunks only when the caller actually needs them.

        EDGAR caps `filings.recent` at roughly 1000 entries and lists the rest as chunk filenames under
        `filings.files`. Merging them unconditionally is a trap: a large bank has 21 such chunks totalling tens
        of megabytes and 21 extra rate-limited requests, while a five-year deep dive needs none of them. So
        chunks are fetched only when `full_history` is asked for, or when `since` reaches back past what the
        recent page already covers.
        """
        data = await self.get_json(SUBMISSIONS_URL.format(cik=cik), self._cache_dir / f'CIK{cik:010d}.json')

        filings = data.get('filings', {})
        recent = {key: list(value) for key, value in filings.get('recent', {}).items()}
        chunks = filings.get('files', [])

        if chunks and self._needs_history(recent, since=since, full_history=full_history):
            log.info(f'CIK {cik}: merging {len(chunks)} older filing chunk(s)')
            for chunk_info in chunks:
                filename = chunk_info['name']
                chunk = await self.get_json(CHUNK_URL.format(filename=filename), self._cache_dir / filename)
                for key in recent:
                    if key in chunk:
                        recent[key].extend(chunk[key])
        elif chunks:
            log.debug(f'CIK {cik}: skipping {len(chunks)} older chunk(s); recent page covers the request')

        data['filings']['recent'] = recent
        return data

    @staticmethod
    def _needs_history(recent: dict, *, since: date | None, full_history: bool) -> bool:
        """True if the request reaches back further than `filings.recent` already covers."""
        if full_history:
            return True
        if since is None:
            return False
        filed = [d for d in (_parse_date(v) for v in recent.get('filingDate', [])) if d is not None]
        return not filed or since < min(filed)

    async def filings(self, cik: int, forms: frozenset[str] | None = None, since: date | None = None,
                      limit: int | None = None, full_history: bool = False) -> list[Filing]:
        """Filings for `cik`, newest first, optionally restricted by form and filing date.

        Defaults to the recent page only, which spans roughly a thousand filings and is far more than any
        single research run reads; `since` or `full_history` pull in the older chunks when genuinely needed.

        Rows without a filing date or a primary document are dropped rather than carried as partial records,
        since neither can be fetched or cited later.
        """
        data = await self.submissions(cik, since=since, full_history=full_history)
        recent = data.get('filings', {}).get('recent', {})
        columns = ('form', 'accessionNumber', 'primaryDocument', 'reportDate', 'filingDate', 'items')
        rows = zip(*(recent.get(name, []) for name in columns), strict=False)

        out: list[Filing] = []
        for form, accession, primary_document, report_date, filing_date, items in rows:
            if forms is not None and form not in forms:
                continue
            filed = _parse_date(filing_date)
            if filed is None or not primary_document:
                continue
            if since is not None and filed < since:
                continue
            out.append(Filing(cik=cik, accession=accession, form=form, filed=filed,
                              report_date=_parse_date(report_date), primary_document=primary_document,
                              items=items or ''))

        out.sort(key=lambda f: f.filed, reverse=True)
        return out[:limit] if limit else out
