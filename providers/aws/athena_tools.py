"""Athena tools: list databases/tables, describe schema, run read-only queries, preview rows.
This is AWS's closest equivalent to BigQuery: a serverless SQL engine over the
Glue Data Catalog. Unlike BigQuery, queries are asynchronous: start, poll, fetch.

Queries are restricted to single read-only statements (see providers/sql_guard.py).
Cost control belongs to the Athena *workgroup* (per-query data-scanned cutoff, enforced
output location): set AWS_ENV_<NAME>_ATHENA_WORKGROUP to a workgroup you've configured."""
import re
import time

from config import CONFIG
from providers.aws.auth import get_client
from providers.aws.paging import list_note, paginate
from providers.output import rows_json
from providers.sql_guard import assert_read_only_sql
from providers.tools import cloud_tool

POLL_INTERVAL_SECONDS = 0.5
MAX_POLL_INTERVAL_SECONDS = 2.0
POLL_TIMEOUT_SECONDS = 60

_IDENTIFIER = re.compile(r"[\w\-]+")


def _identifier(kind: str, value: str) -> str:
    if not _IDENTIFIER.fullmatch(value or ""):
        raise ValueError(f"Invalid {kind} name '{value}': only letters, digits, '_' and '-' are allowed.")
    return value


def _run_query(athena, sql: str, database: str, output_location: str, workgroup: str = None) -> str:
    kwargs = {"QueryString": sql}
    if database:
        kwargs["QueryExecutionContext"] = {"Database": database}
    if output_location:
        kwargs["ResultConfiguration"] = {"OutputLocation": output_location}
    if workgroup:
        kwargs["WorkGroup"] = workgroup

    execution = athena.start_query_execution(**kwargs)
    query_id = execution["QueryExecutionId"]

    waited = 0.0
    interval = POLL_INTERVAL_SECONDS
    while waited < POLL_TIMEOUT_SECONDS:
        status = athena.get_query_execution(QueryExecutionId=query_id)["QueryExecution"]["Status"]
        state = status["State"]
        if state == "SUCCEEDED":
            return query_id
        if state in ("FAILED", "CANCELLED"):
            reason = status.get("StateChangeReason", "unknown reason")
            raise ValueError(f"Athena query {state.lower()}: {reason}")
        time.sleep(interval)
        waited += interval
        interval = min(interval * 1.5, MAX_POLL_INTERVAL_SECONDS)

    # Don't leave it running (and billing) after we've given up waiting.
    try:
        athena.stop_query_execution(QueryExecutionId=query_id)
        stopped = "The query was cancelled."
    except Exception as e:  # best effort
        stopped = f"It could not be cancelled automatically ({type(e).__name__}); it may still be running."
    raise ValueError(
        f"Athena query did not finish within {POLL_TIMEOUT_SECONDS}s (query id: {query_id}). {stopped} "
        "Try a narrower query (add a partition filter or LIMIT)."
    )


def _results_to_rows(athena, query_id: str, max_results: int):
    """Return (rows, more_available). Requests header + max_results + 1 rows so a
    result that is exactly max_results long isn't mistaken for a truncated one."""
    resp = athena.get_query_results(QueryExecutionId=query_id, MaxResults=min(max_results + 2, 1000))
    rows = resp["ResultSet"]["Rows"]
    if not rows:
        return [], False
    header = [c.get("VarCharValue", "") for c in rows[0]["Data"]]
    data = []
    for row in rows[1:]:
        values = [c.get("VarCharValue") for c in row["Data"]]
        data.append(dict(zip(header, values)))
    more = len(data) > max_results or bool(resp.get("NextToken") and len(data) >= max_results)
    return data[:max_results], more


def _query(sql: str, database: str, max_results: int, env: str = None) -> str:
    assert_read_only_sql(sql, "athena")
    max_results = max(1, min(max_results, 100))
    aws_env = CONFIG.resolve_aws(env)
    athena = get_client(aws_env, "athena")
    query_id = _run_query(athena, sql, database, aws_env.athena_output_location, aws_env.athena_workgroup)
    rows, more = _results_to_rows(athena, query_id, max_results)
    if not rows:
        return "Query returned no rows."
    return rows_json(rows, max_results, more, hint="Add a LIMIT/WHERE, or raise max_results (max 100).")


def register(mcp):

    @cloud_tool(mcp)
    def athena_list_databases(env: str = None) -> str:
        """List all Glue/Athena databases. env: a configured AWS environment name (default: the configured default)."""
        aws_env = CONFIG.resolve_aws(env)
        athena = get_client(aws_env, "athena")
        dbs, truncated = paginate(athena, "list_databases", "DatabaseList", CatalogName="AwsDataCatalog")
        if not dbs:
            return "No databases found."
        return "\n".join(f"  • {db['Name']}" for db in dbs) + list_note(len(dbs), truncated)

    @cloud_tool(mcp)
    def athena_list_tables(database: str, env: str = None) -> str:
        """
        List all tables in an Athena/Glue database.

        Args:
            database: e.g. 'my_database'
            env:      a configured AWS environment name (default: the configured default)
        """
        aws_env = CONFIG.resolve_aws(env)
        athena = get_client(aws_env, "athena")
        tables, truncated = paginate(athena, "list_table_metadata", "TableMetadataList",
                                     CatalogName="AwsDataCatalog", DatabaseName=database)
        if not tables:
            return f"No tables found in database '{database}'."
        return "\n".join(f"  • {t['Name']}  ({t.get('TableType', '')})" for t in tables) + list_note(len(tables), truncated)

    @cloud_tool(mcp)
    def athena_describe_table(database: str, table: str, env: str = None) -> str:
        """
        Describe the schema of an Athena table.

        Args:
            database: e.g. 'my_database'
            table:    e.g. 'my_table'
            env:      a configured AWS environment name (default: the configured default)
        """
        aws_env = CONFIG.resolve_aws(env)
        athena = get_client(aws_env, "athena")
        meta = athena.get_table_metadata(CatalogName="AwsDataCatalog", DatabaseName=database, TableName=table)
        table_meta = meta["TableMetadata"]
        lines = [f"Table: {database}.{table}", "", "Schema:"]
        for col in table_meta.get("Columns", []):
            lines.append(f"  {col['Name']:<35} {col.get('Type', ''):<15}")
        partitions = table_meta.get("PartitionKeys", [])
        if partitions:
            lines.append("\nPartition keys:")
            for col in partitions:
                lines.append(f"  {col['Name']:<35} {col.get('Type', ''):<15}")
        return "\n".join(lines)

    @cloud_tool(mcp)
    def athena_query(sql: str, database: str = "", max_results: int = 20, env: str = None) -> str:
        """
        Run a read-only Athena SQL query (one SELECT/WITH/SHOW/DESCRIBE/EXPLAIN statement) and
        return results as JSON. Writes (INSERT, CTAS, UNLOAD, DDL) are rejected. Waits up to 60s;
        a query that takes longer is cancelled.

        Args:
            sql:         The SQL query string.
            database:    Default database for unqualified table names (optional).
            max_results: Maximum rows to return (default 20, max 100). A note is appended when more rows exist.
            env:         a configured AWS environment name (default: the configured default)
        """
        return _query(sql, database, max_results, env)

    @cloud_tool(mcp)
    def athena_get_query_results(query_id: str, max_results: int = 20, env: str = None) -> str:
        """
        Fetch the results of an earlier Athena query by its query execution ID
        (for example one reported in a timeout message that has since finished).

        Args:
            query_id:    The Athena QueryExecutionId.
            max_results: Maximum rows to return (default 20, max 100).
            env:         a configured AWS environment name (default: the configured default)
        """
        max_results = max(1, min(max_results, 100))
        aws_env = CONFIG.resolve_aws(env)
        athena = get_client(aws_env, "athena")
        status = athena.get_query_execution(QueryExecutionId=query_id)["QueryExecution"]["Status"]
        if status["State"] != "SUCCEEDED":
            raise ValueError(f"Query {query_id} is {status['State']}"
                             + (f": {status['StateChangeReason']}" if status.get("StateChangeReason") else "."))
        rows, more = _results_to_rows(athena, query_id, max_results)
        if not rows:
            return "Query returned no rows."
        return rows_json(rows, max_results, more, hint="Raise max_results (max 100).")

    @cloud_tool(mcp)
    def athena_preview_table(database: str, table: str, limit: int = 10, env: str = None) -> str:
        """
        Preview the first N rows of an Athena table.

        Args:
            database: e.g. 'my_database'
            table:    e.g. 'my_table'
            limit:    Number of rows to return (default 10, max 50).
            env:      a configured AWS environment name (default: the configured default)
        """
        limit = max(1, min(limit, 50))
        database = _identifier("database", database)
        table = _identifier("table", table)
        sql = f'SELECT * FROM "{database}"."{table}" LIMIT {limit}'
        return _query(sql, database, limit, env)
