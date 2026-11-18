"""BigQuery tools: list datasets/tables, describe schema, run read-only queries, preview rows.

Safety: queries must be a single read-only statement (providers/sql_guard.py), are
dry-run first (BigQuery reports the real statement type and bytes that would be
scanned), and run with maximum_bytes_billed set from the environment's
GCP_ENV_<NAME>_MAX_BYTES_BILLED (default 10 GB), so a runaway scan fails instead of billing."""
import itertools
import re
from concurrent.futures import TimeoutError as FutureTimeoutError

from google.cloud import bigquery

from config import CONFIG
from providers.gcp.auth import get_credentials
from providers.output import list_note, rows_json
from providers.sql_guard import assert_read_only_sql
from providers.tools import cloud_tool

MAX_LIST_ITEMS = 200
QUERY_TIMEOUT_SECONDS = 60

_IDENTIFIER = re.compile(r"[\w\-]+")


def _identifier(kind: str, value: str) -> str:
    if not _IDENTIFIER.fullmatch(value or ""):
        raise ValueError(f"Invalid {kind} '{value}': only letters, digits, '_' and '-' are allowed.")
    return value


def _client(env: str = None) -> bigquery.Client:
    gcp_env = CONFIG.resolve_gcp(env)
    return bigquery.Client(project=gcp_env.project_id, credentials=get_credentials(gcp_env))


def _first(iterator, limit: int):
    """First `limit` items of an iterator plus whether more existed."""
    items = list(itertools.islice(iterator, limit + 1))
    return items[:limit], len(items) > limit


def _query(sql: str, max_results: int, env: str = None) -> str:
    assert_read_only_sql(sql, "bigquery")
    max_results = max(1, min(max_results, 100))
    gcp_env = CONFIG.resolve_gcp(env)
    client = _client(env)

    dry = client.query(sql, job_config=bigquery.QueryJobConfig(dry_run=True, use_query_cache=False))
    if dry.statement_type != "SELECT":
        raise ValueError(f"Only SELECT queries are allowed; BigQuery classifies this statement as '{dry.statement_type}'.")
    estimate = dry.total_bytes_processed or 0
    if estimate > gcp_env.max_bytes_billed:
        raise ValueError(
            f"Query would process {estimate:,} bytes, over this environment's limit of "
            f"{gcp_env.max_bytes_billed:,} (GCP_ENV_..._MAX_BYTES_BILLED). Filter on a partition/clustering "
            "column, select fewer columns, or raise the limit."
        )

    job = client.query(sql, job_config=bigquery.QueryJobConfig(maximum_bytes_billed=gcp_env.max_bytes_billed))
    try:
        rows = list(job.result(max_results=max_results + 1, timeout=QUERY_TIMEOUT_SECONDS))
    except (FutureTimeoutError, TimeoutError):
        try:
            job.cancel()
        except Exception:
            pass
        raise ValueError(f"BigQuery query did not finish within {QUERY_TIMEOUT_SECONDS}s and was cancelled "
                         f"(job id: {job.job_id}). Try a narrower query.")
    if not rows:
        return "Query returned no rows."
    return rows_json([dict(r) for r in rows], max_results, len(rows) > max_results,
                     hint="Add a LIMIT/WHERE, or raise max_results (max 100).")


def register(mcp):

    @cloud_tool(mcp)
    def bq_list_datasets(env: str = None) -> str:
        """List all BigQuery datasets. env: a configured GCP environment name (default: the configured default)."""
        gcp_env = CONFIG.resolve_gcp(env)
        client = _client(env)
        datasets, truncated = _first(client.list_datasets(), MAX_LIST_ITEMS)
        if not datasets:
            return f"No datasets found in {gcp_env.name} ({gcp_env.project_id})."
        return (f"Project: {gcp_env.project_id}\n" + "\n".join(f"  • {ds.dataset_id}" for ds in datasets)
                + list_note(len(datasets), truncated))

    @cloud_tool(mcp)
    def bq_list_tables(dataset_id: str, env: str = None) -> str:
        """
        List all tables inside a BigQuery dataset.

        Args:
            dataset_id: e.g. 'part_catalog'
            env:        a configured GCP environment name (default: the configured default)
        """
        client = _client(env)
        tables, truncated = _first(client.list_tables(dataset_id), MAX_LIST_ITEMS)
        if not tables:
            return f"No tables found in dataset '{dataset_id}'."
        return "\n".join(f"  • {t.table_id}  ({t.table_type})" for t in tables) + list_note(len(tables), truncated)

    @cloud_tool(mcp)
    def bq_describe_table(dataset_id: str, table_id: str, env: str = None) -> str:
        """
        Describe the schema of a BigQuery table.

        Args:
            dataset_id: e.g. 'part_catalog'
            table_id:   e.g. 'part_level'
            env:        a configured GCP environment name (default: the configured default)
        """
        gcp_env = CONFIG.resolve_gcp(env)
        client = _client(env)
        table = client.get_table(f"{gcp_env.project_id}.{dataset_id}.{table_id}")
        lines = [f"Table: {gcp_env.project_id}.{dataset_id}.{table_id}",
                 f"Rows (approx): {table.num_rows:,}",
                 f"Size: {table.num_bytes / 1024 / 1024:.1f} MB",
                 "", "Schema:"]
        for field in table.schema:
            lines.append(f"  {field.name:<35} {field.field_type:<15} {field.mode}")
        return "\n".join(lines)

    @cloud_tool(mcp)
    def bq_query(sql: str, max_results: int = 20, env: str = None) -> str:
        """
        Run a read-only BigQuery SQL query (a single SELECT/WITH statement) and return results
        as JSON. Writes (INSERT, MERGE, DDL, EXPORT) are rejected. The query is dry-run first and
        refused if it would scan more than the environment's byte limit (default 10 GB).

        Args:
            sql:         The SQL query string. Use full table refs: `project.dataset.table`
            max_results: Maximum rows to return (default 20, max 100). A note is appended when more rows exist.
            env:         a configured GCP environment name (default: the configured default)
        """
        return _query(sql, max_results, env)

    @cloud_tool(mcp)
    def bq_preview_table(dataset_id: str, table_id: str, limit: int = 10, env: str = None) -> str:
        """
        Preview the first N rows of a BigQuery table. Reads rows directly (tabledata.list), which is
        free and never scans the table the way SELECT * would.

        Args:
            dataset_id: e.g. 'part_catalog'
            table_id:   e.g. 'part_level'
            limit:      Number of rows to return (default 10, max 50).
            env:        a configured GCP environment name (default: the configured default)
        """
        limit = max(1, min(limit, 50))
        dataset_id = _identifier("dataset_id", dataset_id)
        table_id = _identifier("table_id", table_id)
        gcp_env = CONFIG.resolve_gcp(env)
        client = _client(env)
        rows = list(client.list_rows(f"{gcp_env.project_id}.{dataset_id}.{table_id}", max_results=limit))
        if not rows:
            return "Table has no rows."
        return rows_json([dict(r) for r in rows], limit, False)
