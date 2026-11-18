import json
from unittest.mock import MagicMock, patch

import boto3
import requests
from moto import mock_aws

from providers.aws import athena_tools
from tests.helpers import get_all_tools, mock_paginators


def _preload_query_results(rows, column_info=None):
    """Push a canned Athena result set into moto's in-memory queue so that the
    next get_query_results call (regardless of QueryExecutionId) returns it.
    See moto's AthenaBackend.get_query_results docstring for this API."""
    if column_info is None:
        column_info = []
    payload = {"results": [{"rows": rows, "column_info": column_info}]}
    resp = requests.post(
        "http://motoapi.amazonaws.com/moto-api/static/athena/query-results",
        json=payload,
    )
    assert resp.status_code == 201, resp.text


# ---------------------------------------------------------------------------
# athena_list_databases
# ---------------------------------------------------------------------------

@mock_aws
def test_athena_list_databases_happy_path(aws_config):
    # moto pre-creates a "default" database under the AwsDataCatalog catalog
    # with no setup required, so this exercises the real code path end to end.
    aws_config(athena_tools)
    tools = get_all_tools(athena_tools)
    result = tools["athena_list_databases"](env="test")
    assert "default" in result
    assert "•" in result


def test_athena_list_databases_empty(aws_config):
    # moto's Athena list_databases always includes a pre-created "default" db
    # under CatalogName="AwsDataCatalog" (the catalog name the source code
    # hardcodes), so the empty-result branch is unreachable through real moto.
    # Falling back to a fully mocked client for just this case.
    aws_config(athena_tools)
    mock_athena = MagicMock()
    mock_paginators(mock_athena, list_databases=[{"DatabaseList": []}])
    with patch("providers.aws.athena_tools.get_client", return_value=mock_athena):
        tools = get_all_tools(athena_tools)
        result = tools["athena_list_databases"](env="test")
    assert result == "No databases found."


def test_athena_list_databases_unknown_environment(aws_config):
    aws_config(athena_tools)
    tools = get_all_tools(athena_tools)
    result = tools["athena_list_databases"](env="does-not-exist")
    assert "Unknown AWS environment" in result
    assert "does-not-exist" in result


# ---------------------------------------------------------------------------
# athena_list_tables
# (moto 5.2.3 raises NotImplementedError for Athena's list_table_metadata,
# confirmed via a throwaway probe script before writing these tests, so this
# tool is tested against a fully mocked athena client per the task's fallback
# guidance.)
# ---------------------------------------------------------------------------

def test_athena_list_tables_happy_path(aws_config):
    aws_config(athena_tools)
    mock_athena = MagicMock()
    mock_paginators(mock_athena, list_table_metadata=[{
        "TableMetadataList": [
            {"Name": "orders", "TableType": "EXTERNAL_TABLE"},
            {"Name": "customers", "TableType": "EXTERNAL_TABLE"},
        ]
    }])
    with patch("providers.aws.athena_tools.get_client", return_value=mock_athena):
        tools = get_all_tools(athena_tools)
        result = tools["athena_list_tables"](database="salesdb", env="test")
    assert "orders" in result
    assert "customers" in result
    assert "EXTERNAL_TABLE" in result
    (call,) = mock_athena.paginate_calls["list_table_metadata"]
    assert call["CatalogName"] == "AwsDataCatalog"
    assert call["DatabaseName"] == "salesdb"


def test_athena_list_tables_empty(aws_config):
    aws_config(athena_tools)
    mock_athena = MagicMock()
    mock_paginators(mock_athena, list_table_metadata=[{"TableMetadataList": []}])
    with patch("providers.aws.athena_tools.get_client", return_value=mock_athena):
        tools = get_all_tools(athena_tools)
        result = tools["athena_list_tables"](database="salesdb", env="test")
    assert result == "No tables found in database 'salesdb'."


def test_athena_list_tables_unknown_environment(aws_config):
    aws_config(athena_tools)
    tools = get_all_tools(athena_tools)
    result = tools["athena_list_tables"](database="salesdb", env="does-not-exist")
    assert "Unknown AWS environment" in result
    assert "does-not-exist" in result


# ---------------------------------------------------------------------------
# athena_describe_table
# (Also relies on the mocked-client fallback: moto raises NotImplementedError
# for Athena's get_table_metadata, same as list_table_metadata above.)
# ---------------------------------------------------------------------------

def test_athena_describe_table_happy_path(aws_config):
    aws_config(athena_tools)
    mock_athena = MagicMock()
    mock_athena.get_table_metadata.return_value = {
        "TableMetadata": {
            "Columns": [
                {"Name": "id", "Type": "bigint"},
                {"Name": "amount", "Type": "double"},
            ],
            "PartitionKeys": [{"Name": "dt", "Type": "string"}],
        }
    }
    with patch("providers.aws.athena_tools.get_client", return_value=mock_athena):
        tools = get_all_tools(athena_tools)
        result = tools["athena_describe_table"](database="salesdb", table="orders", env="test")
    assert "Table: salesdb.orders" in result
    assert "Schema:" in result
    assert "id" in result and "bigint" in result
    assert "amount" in result and "double" in result
    assert "Partition keys:" in result
    assert "dt" in result and "string" in result
    mock_athena.get_table_metadata.assert_called_once_with(
        CatalogName="AwsDataCatalog", DatabaseName="salesdb", TableName="orders"
    )


def test_athena_describe_table_no_partitions(aws_config):
    aws_config(athena_tools)
    mock_athena = MagicMock()
    mock_athena.get_table_metadata.return_value = {
        "TableMetadata": {
            "Columns": [{"Name": "id", "Type": "bigint"}],
            "PartitionKeys": [],
        }
    }
    with patch("providers.aws.athena_tools.get_client", return_value=mock_athena):
        tools = get_all_tools(athena_tools)
        result = tools["athena_describe_table"](database="salesdb", table="orders", env="test")
    assert "Partition keys:" not in result


def test_athena_describe_table_unknown_environment(aws_config):
    aws_config(athena_tools)
    tools = get_all_tools(athena_tools)
    result = tools["athena_describe_table"](database="salesdb", table="orders", env="does-not-exist")
    assert "Unknown AWS environment" in result
    assert "does-not-exist" in result


# ---------------------------------------------------------------------------
# athena_query
# ---------------------------------------------------------------------------

@mock_aws
def test_athena_query_no_rows(aws_config):
    aws_config(athena_tools)
    tools = get_all_tools(athena_tools)
    result = tools["athena_query"](sql="SELECT * FROM empty_table", database="salesdb", env="test")
    assert result == "Query returned no rows."


@mock_aws
def test_athena_query_happy_path_returns_json(aws_config):
    aws_config(athena_tools)
    _preload_query_results(
        rows=[
            {"Data": [{"VarCharValue": "id"}, {"VarCharValue": "name"}]},
            {"Data": [{"VarCharValue": "1"}, {"VarCharValue": "alice"}]},
            {"Data": [{"VarCharValue": "2"}, {"VarCharValue": "bob"}]},
        ]
    )
    tools = get_all_tools(athena_tools)
    result = tools["athena_query"](sql="SELECT id, name FROM users", database="salesdb", env="test")
    parsed = json.loads(result)
    assert parsed == [
        {"id": "1", "name": "alice"},
        {"id": "2", "name": "bob"},
    ]


@mock_aws
def test_athena_query_max_results_limits_rows_returned(aws_config):
    aws_config(athena_tools)
    header = {"Data": [{"VarCharValue": "n"}]}
    data_rows = [{"Data": [{"VarCharValue": str(i)}]} for i in range(10)]
    _preload_query_results(rows=[header] + data_rows)

    tools = get_all_tools(athena_tools)
    result = tools["athena_query"](sql="SELECT n FROM numbers", database="salesdb", max_results=3, env="test")
    body, note = result.split("\n[", 1)
    assert json.loads(body) == [{"n": "0"}, {"n": "1"}, {"n": "2"}]
    # rows were cut off, so the result must say so (a model would otherwise assume it is complete)
    assert "Showing the first 3 rows; more rows are available" in note


def test_athena_query_max_results_clamped_to_100(aws_config):
    aws_config(athena_tools)
    mock_athena = MagicMock()
    mock_athena.start_query_execution.return_value = {"QueryExecutionId": "qid-1"}
    mock_athena.get_query_execution.return_value = {
        "QueryExecution": {"Status": {"State": "SUCCEEDED"}}
    }
    mock_athena.get_query_results.return_value = {"ResultSet": {"Rows": []}}
    with patch("providers.aws.athena_tools.get_client", return_value=mock_athena):
        tools = get_all_tools(athena_tools)
        tools["athena_query"](sql="SELECT 1", max_results=150, env="test")
    # max_results should have been clamped to 100 before being used, so the
    # underlying fetch asks for header + 100 + 1 extra (to detect truncation) = 102, not 152.
    called_kwargs = mock_athena.get_query_results.call_args.kwargs
    assert called_kwargs["MaxResults"] == 102


@mock_aws
def test_athena_query_uses_output_location_when_configured(aws_config):
    aws_env = aws_config(athena_tools)
    aws_env.athena_output_location = "s3://my-athena-results/"
    tools = get_all_tools(athena_tools)
    result = tools["athena_query"](sql="SELECT 1", env="test")
    assert result == "Query returned no rows."


def test_athena_query_failed_state_returns_error(aws_config):
    aws_config(athena_tools)
    mock_athena = MagicMock()
    mock_athena.start_query_execution.return_value = {"QueryExecutionId": "qid-failed"}
    mock_athena.get_query_execution.return_value = {
        "QueryExecution": {
            "Status": {"State": "FAILED", "StateChangeReason": "Syntax error in query"}
        }
    }
    with patch("providers.aws.athena_tools.get_client", return_value=mock_athena):
        tools = get_all_tools(athena_tools)
        result = tools["athena_query"](sql="SELECT * FROM bad syntax", env="test")
    assert result.startswith("Error:")
    assert "failed" in result.lower()
    assert "Syntax error in query" in result


def test_athena_query_cancelled_state_returns_error_with_default_reason(aws_config):
    aws_config(athena_tools)
    mock_athena = MagicMock()
    mock_athena.start_query_execution.return_value = {"QueryExecutionId": "qid-cancelled"}
    mock_athena.get_query_execution.return_value = {
        "QueryExecution": {"Status": {"State": "CANCELLED"}}
    }
    with patch("providers.aws.athena_tools.get_client", return_value=mock_athena):
        tools = get_all_tools(athena_tools)
        result = tools["athena_query"](sql="SELECT 1", env="test")
    assert result.startswith("Error:")
    assert "cancelled" in result.lower()
    assert "unknown reason" in result.lower()


def test_athena_query_unknown_environment(aws_config):
    aws_config(athena_tools)
    tools = get_all_tools(athena_tools)
    result = tools["athena_query"](sql="SELECT 1", env="does-not-exist")
    assert "Unknown AWS environment" in result
    assert "does-not-exist" in result


# ---------------------------------------------------------------------------
# athena_preview_table
# ---------------------------------------------------------------------------

@mock_aws
def test_athena_preview_table_no_rows(aws_config):
    aws_config(athena_tools)
    tools = get_all_tools(athena_tools)
    result = tools["athena_preview_table"](database="salesdb", table="orders", env="test")
    assert result == "Query returned no rows."


@mock_aws
def test_athena_preview_table_happy_path_returns_json(aws_config):
    aws_config(athena_tools)
    _preload_query_results(
        rows=[
            {"Data": [{"VarCharValue": "id"}, {"VarCharValue": "amount"}]},
            {"Data": [{"VarCharValue": "1"}, {"VarCharValue": "9.99"}]},
        ]
    )
    tools = get_all_tools(athena_tools)
    result = tools["athena_preview_table"](database="salesdb", table="orders", limit=5, env="test")
    parsed = json.loads(result)
    assert parsed == [{"id": "1", "amount": "9.99"}]


def test_athena_preview_table_limit_clamped_and_builds_sql(aws_config):
    aws_config(athena_tools)
    mock_athena = MagicMock()
    mock_athena.start_query_execution.return_value = {"QueryExecutionId": "qid-preview"}
    mock_athena.get_query_execution.return_value = {
        "QueryExecution": {"Status": {"State": "SUCCEEDED"}}
    }
    mock_athena.get_query_results.return_value = {"ResultSet": {"Rows": []}}
    with patch("providers.aws.athena_tools.get_client", return_value=mock_athena):
        tools = get_all_tools(athena_tools)
        result = tools["athena_preview_table"](database="salesdb", table="orders", limit=999, env="test")
    assert result == "Query returned no rows."
    called_sql = mock_athena.start_query_execution.call_args.kwargs["QueryString"]
    assert 'LIMIT 50' in called_sql
    assert '"salesdb"."orders"' in called_sql
    # The clamped limit (50) is also what gets passed through as max_results.
    called_max_results = mock_athena.get_query_results.call_args.kwargs["MaxResults"]
    assert called_max_results == 50 + 2


def test_athena_preview_table_unknown_environment(aws_config):
    aws_config(athena_tools)
    tools = get_all_tools(athena_tools)
    result = tools["athena_preview_table"](database="salesdb", table="orders", env="does-not-exist")
    assert "Unknown AWS environment" in result
    assert "does-not-exist" in result


# ---------------------------------------------------------------------------
# read-only enforcement, workgroup, timeout cancellation, get_query_results
# ---------------------------------------------------------------------------

import pytest

from mcp.server.fastmcp.exceptions import ToolError


@pytest.mark.parametrize("sql", [
    "DROP TABLE orders",
    "INSERT INTO orders VALUES (1)",
    "CREATE TABLE x AS SELECT * FROM orders",
    "UNLOAD (SELECT * FROM orders) TO 's3://bucket/exfil/' WITH (format = 'JSON')",
    "WITH t AS (SELECT 1) INSERT INTO orders SELECT * FROM t",
    "SELECT 1; DROP TABLE orders",
    "EXPLAIN ANALYZE INSERT INTO orders SELECT 1",
])
def test_athena_query_rejects_writes_before_calling_aws(aws_config, sql):
    aws_config(athena_tools)
    mock_athena = MagicMock()
    with patch("providers.aws.athena_tools.get_client", return_value=mock_athena):
        tools = get_all_tools(athena_tools, raise_errors=True)
        with pytest.raises(ToolError):
            tools["athena_query"](sql=sql, env="test")
    mock_athena.start_query_execution.assert_not_called()


def test_athena_query_passes_workgroup_when_configured(aws_config):
    aws_env = aws_config(athena_tools)
    aws_env.athena_workgroup = "capped-wg"
    mock_athena = MagicMock()
    mock_athena.start_query_execution.return_value = {"QueryExecutionId": "q1"}
    mock_athena.get_query_execution.return_value = {"QueryExecution": {"Status": {"State": "SUCCEEDED"}}}
    mock_athena.get_query_results.return_value = {"ResultSet": {"Rows": []}}
    with patch("providers.aws.athena_tools.get_client", return_value=mock_athena):
        get_all_tools(athena_tools)["athena_query"](sql="SELECT 1", env="test")
    assert mock_athena.start_query_execution.call_args.kwargs["WorkGroup"] == "capped-wg"


def test_athena_query_without_workgroup_sends_none(aws_config):
    aws_config(athena_tools)
    mock_athena = MagicMock()
    mock_athena.start_query_execution.return_value = {"QueryExecutionId": "q1"}
    mock_athena.get_query_execution.return_value = {"QueryExecution": {"Status": {"State": "SUCCEEDED"}}}
    mock_athena.get_query_results.return_value = {"ResultSet": {"Rows": []}}
    with patch("providers.aws.athena_tools.get_client", return_value=mock_athena):
        get_all_tools(athena_tools)["athena_query"](sql="SELECT 1", env="test")
    assert "WorkGroup" not in mock_athena.start_query_execution.call_args.kwargs


def test_athena_timeout_cancels_the_running_query(aws_config, monkeypatch):
    aws_config(athena_tools)
    monkeypatch.setattr(athena_tools, "POLL_TIMEOUT_SECONDS", 0.05)
    monkeypatch.setattr(athena_tools, "POLL_INTERVAL_SECONDS", 0.02)
    monkeypatch.setattr(athena_tools, "MAX_POLL_INTERVAL_SECONDS", 0.02)
    mock_athena = MagicMock()
    mock_athena.start_query_execution.return_value = {"QueryExecutionId": "slow-q"}
    mock_athena.get_query_execution.return_value = {"QueryExecution": {"Status": {"State": "RUNNING"}}}
    with patch("providers.aws.athena_tools.get_client", return_value=mock_athena):
        result = get_all_tools(athena_tools)["athena_query"](sql="SELECT 1", env="test")
    assert "did not finish" in result and "slow-q" in result and "cancelled" in result
    mock_athena.stop_query_execution.assert_called_once_with(QueryExecutionId="slow-q")


def test_athena_exact_max_results_is_not_reported_as_truncated(aws_config):
    aws_config(athena_tools)
    mock_athena = MagicMock()
    mock_athena.start_query_execution.return_value = {"QueryExecutionId": "q1"}
    mock_athena.get_query_execution.return_value = {"QueryExecution": {"Status": {"State": "SUCCEEDED"}}}
    mock_athena.get_query_results.return_value = {"ResultSet": {"Rows": [
        {"Data": [{"VarCharValue": "n"}]}, {"Data": [{"VarCharValue": "1"}]}, {"Data": [{"VarCharValue": "2"}]},
    ]}}
    with patch("providers.aws.athena_tools.get_client", return_value=mock_athena):
        result = get_all_tools(athena_tools)["athena_query"](sql="SELECT n FROM t", max_results=2, env="test")
    assert json.loads(result) == [{"n": "1"}, {"n": "2"}]  # pure JSON, no truncation note


def test_athena_long_cells_are_clipped(aws_config):
    aws_config(athena_tools)
    mock_athena = MagicMock()
    mock_athena.start_query_execution.return_value = {"QueryExecutionId": "q1"}
    mock_athena.get_query_execution.return_value = {"QueryExecution": {"Status": {"State": "SUCCEEDED"}}}
    mock_athena.get_query_results.return_value = {"ResultSet": {"Rows": [
        {"Data": [{"VarCharValue": "blob"}]}, {"Data": [{"VarCharValue": "x" * 5000}]},
    ]}}
    with patch("providers.aws.athena_tools.get_client", return_value=mock_athena):
        result = get_all_tools(athena_tools)["athena_query"](sql="SELECT blob FROM t", env="test")
    cell = json.loads(result)[0]["blob"]
    assert len(cell) < 2100 and "truncated 3000 chars" in cell


def test_athena_list_databases_notes_truncation(aws_config):
    aws_config(athena_tools)
    mock_athena = MagicMock()
    mock_paginators(mock_athena, list_databases=[{"DatabaseList": [{"Name": f"db{i}"} for i in range(201)]}])
    with patch("providers.aws.athena_tools.get_client", return_value=mock_athena):
        result = get_all_tools(athena_tools)["athena_list_databases"](env="test")
    assert "db199" in result and "db200" not in result
    assert "more exist" in result


def test_athena_preview_rejects_unsafe_identifiers(aws_config):
    aws_config(athena_tools)
    mock_athena = MagicMock()
    with patch("providers.aws.athena_tools.get_client", return_value=mock_athena):
        tools = get_all_tools(athena_tools, raise_errors=True)
        with pytest.raises(ToolError, match="Invalid table"):
            tools["athena_preview_table"](database="salesdb", table='orders"; DROP TABLE x; --', env="test")
    mock_athena.start_query_execution.assert_not_called()


def test_athena_get_query_results_returns_rows_for_finished_query(aws_config):
    aws_config(athena_tools)
    mock_athena = MagicMock()
    mock_athena.get_query_execution.return_value = {"QueryExecution": {"Status": {"State": "SUCCEEDED"}}}
    mock_athena.get_query_results.return_value = {"ResultSet": {"Rows": [
        {"Data": [{"VarCharValue": "n"}]}, {"Data": [{"VarCharValue": "7"}]},
    ]}}
    with patch("providers.aws.athena_tools.get_client", return_value=mock_athena):
        result = get_all_tools(athena_tools)["athena_get_query_results"](query_id="q-old", env="test")
    assert json.loads(result) == [{"n": "7"}]


def test_athena_get_query_results_reports_unfinished_query(aws_config):
    aws_config(athena_tools)
    mock_athena = MagicMock()
    mock_athena.get_query_execution.return_value = {"QueryExecution": {"Status": {"State": "CANCELLED"}}}
    with patch("providers.aws.athena_tools.get_client", return_value=mock_athena):
        result = get_all_tools(athena_tools)["athena_get_query_results"](query_id="q-old", env="test")
    assert "CANCELLED" in result
    mock_athena.get_query_results.assert_not_called()
