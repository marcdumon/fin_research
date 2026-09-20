"""DuckDB store for everything the ingestion layer produces, plus read-only access to the market data next door.

Two rules shape the schema.

Every document row carries `cleaner_version` and `filed`. The version matters because a citation is an offset
into one exact string: change how text is rendered and every stored offset silently points somewhere else, so
evidence is only valid against the version that produced it. The filing date matters because it is the only
record of when a fact became knowable, and it cannot be reconstructed afterwards.

Evidence is keyed by document, never by accession. A filing contains the primary document and a rendered page
per statement, each with its own coordinate system; keying citations by accession would make offsets from one
document resolve, wrongly, against another.

Prices and fundamentals are not copied here. The neighbouring workbench already holds them, so its database is
attached read-only -- one source of truth, no synchronisation, and no write contention against a file that
allows a single writer.
"""

import hashlib
import logging
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import duckdb
import pandas as pd

from fin_research.config import Config, read
from fin_research.ingest.edgar import Filing
from fin_research.ingest.facts import Fact
from fin_research.ingest.sections import SplitResult
from fin_research.ingest.text import CleanedDocument, estimate_tokens

log = logging.getLogger(__name__)

MARKET_ALIAS = 'iv'

SCHEMA = """
CREATE TABLE IF NOT EXISTS filings (
    accession        VARCHAR PRIMARY KEY,
    cik              BIGINT  NOT NULL,
    ticker           VARCHAR,
    form             VARCHAR NOT NULL,
    filed            DATE    NOT NULL,
    report_date      DATE,
    primary_document VARCHAR NOT NULL,
    items            VARCHAR,
    base_url         VARCHAR NOT NULL,
    discovered_at    TIMESTAMP NOT NULL
);

CREATE TABLE IF NOT EXISTS documents (
    document_id     VARCHAR PRIMARY KEY,
    accession       VARCHAR NOT NULL,
    cik             BIGINT  NOT NULL,
    filename        VARCHAR NOT NULL,
    doc_role        VARCHAR NOT NULL,
    url             VARCHAR NOT NULL,
    filed           DATE    NOT NULL,
    cleaner_version VARCHAR NOT NULL,
    text            VARCHAR NOT NULL,
    n_chars         INTEGER NOT NULL,
    est_tokens      INTEGER NOT NULL,
    fetched_at      TIMESTAMP NOT NULL
);

CREATE TABLE IF NOT EXISTS sections (
    section_id  VARCHAR PRIMARY KEY,
    document_id VARCHAR NOT NULL,
    accession   VARCHAR NOT NULL,
    item_key    VARCHAR NOT NULL,
    title       VARCHAR,
    char_start  INTEGER NOT NULL,
    char_end    INTEGER NOT NULL,
    n_chars     INTEGER NOT NULL,
    est_tokens  INTEGER NOT NULL,
    method      VARCHAR NOT NULL,
    confidence  DOUBLE  NOT NULL,
    is_redirect BOOLEAN NOT NULL,
    redirect_to VARCHAR
);

CREATE TABLE IF NOT EXISTS facts (
    fact_id      VARCHAR PRIMARY KEY,
    cik          BIGINT  NOT NULL,
    ticker       VARCHAR,
    accession    VARCHAR NOT NULL,
    taxonomy     VARCHAR NOT NULL,
    tag          VARCHAR NOT NULL,
    metric       VARCHAR,
    statement    VARCHAR,
    unit         VARCHAR NOT NULL,
    value        DOUBLE  NOT NULL,
    period_start DATE,
    period_end   DATE    NOT NULL,
    period_type  VARCHAR NOT NULL,
    form         VARCHAR NOT NULL,
    filed        DATE    NOT NULL,
    anchor_rank  INTEGER NOT NULL
);

CREATE INDEX IF NOT EXISTS ix_documents_accession ON documents (accession);
CREATE INDEX IF NOT EXISTS ix_sections_document   ON sections (document_id);
CREATE INDEX IF NOT EXISTS ix_facts_metric        ON facts (cik, metric, period_end);
CREATE INDEX IF NOT EXISTS ix_facts_filed         ON facts (cik, filed);
"""


def digest(*parts: object) -> str:
    """Short content-addressed id, so re-ingesting the same thing converges instead of duplicating."""
    joined = '|'.join(str(part) for part in parts)
    return hashlib.sha256(joined.encode()).hexdigest()[:16]


def connect(cfg: Config | None = None, *, read_only: bool = False) -> duckdb.DuckDBPyConnection:
    """Open the store, creating its schema on first use.

    `read_only=True` for anything that only queries. DuckDB allows one writing process, so a reader that asks
    for write access blocks every other reader for no reason.
    """
    cfg = cfg or read()
    cfg.data_root.mkdir(parents=True, exist_ok=True)
    con = duckdb.connect(str(cfg.db_path), read_only=read_only)
    if not read_only:
        con.execute(SCHEMA)
    return con


def scalar(con: duckdb.DuckDBPyConnection, sql: str, params: list | None = None) -> Any:
    """First column of the first row `sql` returns."""
    row = con.execute(sql, params or []).fetchone()
    if row is None:
        raise ValueError(f'query returned no rows: {sql}')
    return row[0]


def count_rows(con: duckdb.DuckDBPyConnection, table: str) -> int:
    """Row count of `table`, or 0 if it does not exist."""
    exists = con.execute('SELECT 1 FROM information_schema.tables WHERE table_name = ?', [table]).fetchone()
    return int(scalar(con, f'SELECT count(*) FROM {table}')) if exists else 0


def store(con: duckdb.DuckDBPyConnection, table: str, frame: pd.DataFrame, key: list[str]) -> int:
    """Merge-upsert `frame` into `table` on `key`, and return the table's row count.

    Lifted from the neighbouring workbench, where it is the single path by which anything reaches the database.
    Matched keys update, new keys insert, so re-ingesting a company converges rather than duplicating -- which
    is what makes a failed run safe to simply repeat.
    """
    if frame.empty:
        return count_rows(con, table)

    columns = list(frame.columns)
    non_key = [c for c in columns if c not in key]
    con.register('_incoming', frame)
    try:
        on = ' AND '.join(f't."{c}" = s."{c}"' for c in key)
        updates = ', '.join(f'"{c}" = s."{c}"' for c in non_key)
        insert_columns = ', '.join(f'"{c}"' for c in columns)
        insert_values = ', '.join(f's."{c}"' for c in columns)
        distinct_on = ', '.join(f'"{c}"' for c in key)
        con.execute(f"""
            MERGE INTO {table} t
            USING (SELECT DISTINCT ON ({distinct_on}) * FROM _incoming) s
            ON {on}
            WHEN MATCHED THEN UPDATE SET {updates}
            WHEN NOT MATCHED THEN INSERT ({insert_columns}) VALUES ({insert_values})
        """)
    finally:
        con.unregister('_incoming')
    return count_rows(con, table)


def attach_market_data(con: duckdb.DuckDBPyConnection, path: Path, alias: str = MARKET_ALIAS) -> bool:
    """Attach the neighbouring workbench's database read-only, returning whether it succeeded.

    Failure is expected and survivable: the file is single-writer, so an ingest running next door holds the
    lock and this will refuse. Callers get False and a clear warning rather than an exception, because a
    research run can still read filings without prices -- but it must know that it is doing so.
    """
    if not path.exists():
        log.warning(f'market database not found at {path}; continuing without prices')
        return False
    try:
        con.execute(f"ATTACH '{path}' AS {alias} (READ_ONLY)")
    except duckdb.Error as error:
        log.warning(f'could not attach {path}: {error}; continuing without prices')
        return False
    return True


# --- writing ------------------------------------------------------------------
def store_filings(con: duckdb.DuckDBPyConnection, filings: list[Filing], ticker: str | None = None) -> int:
    """Record which filings exist for a company."""
    now = datetime.now(UTC)
    frame = pd.DataFrame([{
        'accession': f.accession, 'cik': f.cik, 'ticker': ticker, 'form': f.form, 'filed': f.filed,
        'report_date': f.report_date, 'primary_document': f.primary_document, 'items': f.items,
        'base_url': f.base_url, 'discovered_at': now,
    } for f in filings])
    return store(con, 'filings', frame, ['accession'])


def store_document(con: duckdb.DuckDBPyConnection, filing: Filing, filename: str, doc_role: str,
                   document: CleanedDocument) -> str:
    """Record one document's canonical text, and return its id.

    The id is derived from the accession and filename, so the same document always lands on the same row and
    the sections referring to it stay valid across re-ingestion.
    """
    document_id = digest(filing.accession, filename)
    frame = pd.DataFrame([{
        'document_id': document_id, 'accession': filing.accession, 'cik': filing.cik, 'filename': filename,
        'doc_role': doc_role, 'url': f'{filing.base_url}/{filename}', 'filed': filing.filed,
        'cleaner_version': document.cleaner_version, 'text': document.text, 'n_chars': len(document.text),
        'est_tokens': estimate_tokens(document.text), 'fetched_at': datetime.now(UTC),
    }])
    store(con, 'documents', frame, ['document_id'])
    return document_id


def store_sections(con: duckdb.DuckDBPyConnection, document_id: str, accession: str, split: SplitResult) -> int:
    """Record where each item of a filing begins and ends within one document's text."""
    frame = pd.DataFrame([{
        'section_id': digest(document_id, s.item_key), 'document_id': document_id, 'accession': accession,
        'item_key': s.item_key, 'title': s.title, 'char_start': s.char_start, 'char_end': s.char_end,
        'n_chars': s.n_chars, 'est_tokens': s.est_tokens, 'method': s.method,
        'confidence': split.confidence, 'is_redirect': s.is_redirect, 'redirect_to': s.redirect_to,
    } for s in split.sections])
    return store(con, 'sections', frame, ['section_id'])


def store_facts(con: duckdb.DuckDBPyConnection, facts: list[Fact], ticker: str | None = None) -> int:
    """Record normalised metrics, keyed so a restatement updates in place rather than duplicating."""
    frame = pd.DataFrame([{
        'fact_id': digest(f.accession, f.tag, f.unit, f.period_start, f.period_end),
        'cik': f.cik, 'ticker': ticker, 'accession': f.accession, 'taxonomy': f.taxonomy, 'tag': f.tag,
        'metric': f.metric, 'statement': f.statement, 'unit': f.unit, 'value': f.value,
        'period_start': f.period_start, 'period_end': f.period_end, 'period_type': f.period_type,
        'form': f.form, 'filed': f.filed, 'anchor_rank': f.anchor_rank,
    } for f in facts])
    return store(con, 'facts', frame, ['fact_id'])


# --- reading ------------------------------------------------------------------
def section_text(con: duckdb.DuckDBPyConnection, section_id: str) -> str | None:
    """The text of one stored section, sliced from the document it was measured against."""
    row = con.execute("""
        SELECT substr(d.text, s.char_start + 1, s.char_end - s.char_start)
        FROM sections s JOIN documents d USING (document_id)
        WHERE s.section_id = ?
    """, [section_id]).fetchone()
    return row[0] if row else None
