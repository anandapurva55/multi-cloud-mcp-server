"""Tests for providers/gcp/bigquery_tools.py: list datasets/tables, describe schema,
run queries, preview rows. All GCP calls are mocked at the bigquery.Client boundary
and providers.gcp.bigquery_tools.get_credentials is mocked so no real ADC call happens.
"""
import json
from concurrent.futures import TimeoutError as FutureTimeoutError
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from google.api_core.exceptions import BadRequest, NotFound

from providers.gcp import bigquery_tools
from tests.helpers import get_all_tools


# ---------------------------------------------------------------------------
# bq_list_datasets
# ---------------------------------------------------------------------------

def test_bq_list_datasets_empty(gcp_config):
    gcp_config(bigquery_tools)
    with patch("providers.gcp.bigquery_tools.bigquery.Client") as MockClient, \
            patch("providers.gcp.bigquery_tools.get_credentials", return_value=MagicMock()):
        MockClient.return_value.list_datasets.return_value = []
        tools = get_all_tools(bigquery_tools)
        result = tools["bq_list_datasets"](env="test")
        assert result == "No datasets found in test (test-project)."


def test_bq_list_datasets_with_data(gcp_config):
    gcp_config(bigquery_tools)
    fake_creds = MagicMock()
    with patch("providers.gcp.bigquery_tools.bigquery.Client") as MockClient, \
            patch("providers.gcp.bigquery_tools.get_credentials", return_value=fake_creds):
        MockClient.return_value.list_datasets.return_value = [
            SimpleNamespace(dataset_id="part_catalog"),
            SimpleNamespace(dataset_id="analytics"),
        ]
        tools = get_all_tools(bigquery_tools)
        result = tools["bq_list_datasets"](env="test")
        assert "Project: test-project" in result
        assert "part_catalog" in result
        assert "analytics" in result
        MockClient.assert_called_once_with(project="test-project", credentials=fake_creds)


def test_bq_list_datasets_unknown_env(gcp_config):
    gcp_config(bigquery_tools)
    tools = get_all_tools(bigquery_tools)
    result = tools["bq_list_datasets"](env="does-not-exist")
    assert "Unknown GCP environment" in result
    assert "does-not-exist" in result


# ---------------------------------------------------------------------------
# bq_list_tables
# ---------------------------------------------------------------------------

def test_bq_list_tables_empty(gcp_config):
    gcp_config(bigquery_tools)
    with patch("providers.gcp.bigquery_tools.bigquery.Client") as MockClient, \
            patch("providers.gcp.bigquery_tools.get_credentials", return_value=MagicMock()):
        MockClient.return_value.list_tables.return_value = []
        tools = get_all_tools(bigquery_tools)
        result = tools["bq_list_tables"](dataset_id="part_catalog", env="test")
        assert result == "No tables found in dataset 'part_catalog'."


def test_bq_list_tables_with_data(gcp_config):
    gcp_config(bigquery_tools)
    with patch("providers.gcp.bigquery_tools.bigquery.Client") as MockClient, \
            patch("providers.gcp.bigquery_tools.get_credentials", return_value=MagicMock()):
        MockClient.return_value.list_tables.return_value = [
            SimpleNamespace(table_id="part_level", table_type="TABLE"),
            SimpleNamespace(table_id="part_view", table_type="VIEW"),
        ]
        tools = get_all_tools(bigquery_tools)
        result = tools["bq_list_tables"](dataset_id="part_catalog", env="test")
        assert "part_level  (TABLE)" in result
        assert "part_view  (VIEW)" in result
        MockClient.return_value.list_tables.assert_called_once_with("part_catalog")


def test_bq_list_tables_unknown_env(gcp_config):
    gcp_config(bigquery_tools)
    tools = get_all_tools(bigquery_tools)
    result = tools["bq_list_tables"](dataset_id="part_catalog", env="does-not-exist")
    assert "Unknown GCP environment" in result
    assert "does-not-exist" in result


# ---------------------------------------------------------------------------
# bq_describe_table
# ---------------------------------------------------------------------------

def test_bq_describe_table_returns_schema(gcp_config):
    gcp_config(bigquery_tools)
    with patch("providers.gcp.bigquery_tools.bigquery.Client") as MockClient, \
            patch("providers.gcp.bigquery_tools.get_credentials", return_value=MagicMock()):
        fake_table = SimpleNamespace(
            num_rows=1234567,
            num_bytes=1024 * 1024 * 5,
            schema=[
                SimpleNamespace(name="id", field_type="INTEGER", mode="REQUIRED"),
                SimpleNamespace(name="name", field_type="STRING", mode="NULLABLE"),
            ],
        )
        MockClient.return_value.get_table.return_value = fake_table
        tools = get_all_tools(bigquery_tools)
        result = tools["bq_describe_table"](dataset_id="part_catalog", table_id="part_level", env="test")
        assert "Table: test-project.part_catalog.part_level" in result
        assert "Rows (approx): 1,234,567" in result
        assert "Size: 5.0 MB" in result
        assert "id" in result and "INTEGER" in result and "REQUIRED" in result
        assert "name" in result and "STRING" in result and "NULLABLE" in result
        MockClient.return_value.get_table.assert_called_once_with(
            "test-project.part_catalog.part_level"
        )


def test_bq_describe_table_not_found(gcp_config):
    gcp_config(bigquery_tools)
    with patch("providers.gcp.bigquery_tools.bigquery.Client") as MockClient, \
            patch("providers.gcp.bigquery_tools.get_credentials", return_value=MagicMock()):
        MockClient.return_value.get_table.side_effect = NotFound("Table missing.part_level not found")
        tools = get_all_tools(bigquery_tools)
        result = tools["bq_describe_table"](dataset_id="missing", table_id="part_level", env="test")
        assert result.startswith("Error:") and "NotFound" in result
        assert "NotFound" in result


def test_bq_describe_table_unknown_env(gcp_config):
    gcp_config(bigquery_tools)
    tools = get_all_tools(bigquery_tools)
    result = tools["bq_describe_table"](dataset_id="d", table_id="t", env="does-not-exist")
    assert "Unknown GCP environment" in result
    assert "does-not-exist" in result


# ---------------------------------------------------------------------------
# bq_query
# ---------------------------------------------------------------------------

def make_bq_client(rows=None, statement_type="SELECT", bytes_estimate=1_000, query_side_effect=None):
    """A bigquery.Client stand-in: dry-run jobs report statement type + bytes estimate, real
    jobs return `rows` from .result(). Returns (client, real_job)."""
    client = MagicMock()
    real_job = MagicMock()
    real_job.result.return_value = rows if rows is not None else []
    real_job.job_id = "job-123"
    dry_job = MagicMock(statement_type=statement_type, total_bytes_processed=bytes_estimate)

    def query(sql, job_config=None):
        if query_side_effect:
            raise query_side_effect
        return dry_job if getattr(job_config, "dry_run", False) else real_job

    client.query.side_effect = query
    return client, real_job


def _run_bq(gcp_config, client, tool, **kwargs):
    gcp_config(bigquery_tools)
    with patch("providers.gcp.bigquery_tools.bigquery.Client", return_value=client), \
            patch("providers.gcp.bigquery_tools.get_credentials", return_value=MagicMock()):
        return get_all_tools(bigquery_tools)[tool](env="test", **kwargs)


def test_bq_query_returns_json(gcp_config):
    client, job = make_bq_client(rows=[{"id": 1, "name": "alice"}, {"id": 2, "name": "bob"}])
    result = _run_bq(gcp_config, client, "bq_query", sql="SELECT id, name FROM t")
    assert json.loads(result) == [{"id": 1, "name": "alice"}, {"id": 2, "name": "bob"}]
    # one extra row is requested so a result of exactly max_results isn't mistaken for a truncated one
    assert job.result.call_args.kwargs["max_results"] == 21
    assert job.result.call_args.kwargs["timeout"] == bigquery_tools.QUERY_TIMEOUT_SECONDS


def test_bq_query_no_rows(gcp_config):
    client, _ = make_bq_client(rows=[])
    assert _run_bq(gcp_config, client, "bq_query", sql="SELECT 1 WHERE FALSE") == "Query returned no rows."


def test_bq_query_max_results_clamped(gcp_config):
    client, job = make_bq_client(rows=[{"id": 1}])
    _run_bq(gcp_config, client, "bq_query", sql="SELECT * FROM t", max_results=500)
    assert job.result.call_args.kwargs["max_results"] == 101  # clamped to 100, +1 to detect truncation


def test_bq_query_discloses_truncation(gcp_config):
    client, _ = make_bq_client(rows=[{"n": i} for i in range(4)])  # 3 requested + 1 probe row
    result = _run_bq(gcp_config, client, "bq_query", sql="SELECT n FROM t", max_results=3)
    body, note = result.split("\n[", 1)
    assert len(json.loads(body)) == 3
    assert "Showing the first 3 rows; more rows are available" in note


def test_bq_query_sets_maximum_bytes_billed_from_environment(gcp_config):
    client, _ = make_bq_client(rows=[{"n": 1}])
    env = gcp_config(bigquery_tools)
    env.max_bytes_billed = 5_000_000
    with patch("providers.gcp.bigquery_tools.bigquery.Client", return_value=client), \
            patch("providers.gcp.bigquery_tools.get_credentials", return_value=MagicMock()):
        get_all_tools(bigquery_tools)["bq_query"](sql="SELECT 1", env="test")
    configs = [c.kwargs["job_config"] for c in client.query.call_args_list]
    dry, real = configs
    assert dry.dry_run is True
    assert real.maximum_bytes_billed == 5_000_000


def test_bq_query_refused_when_dry_run_estimate_exceeds_limit(gcp_config):
    client, job = make_bq_client(bytes_estimate=50_000_000_000)  # 50 GB > default 10 GB
    result = _run_bq(gcp_config, client, "bq_query", sql="SELECT * FROM big")
    assert "would process 50,000,000,000 bytes" in result
    job.result.assert_not_called()
    assert client.query.call_count == 1  # only the free dry run happened; the real job was never started


@pytest.mark.parametrize("sql", [
    "DROP TABLE ds.t",
    "INSERT INTO ds.t VALUES (1)",
    "MERGE ds.t USING ds.s ON TRUE WHEN MATCHED THEN DELETE",
    "CREATE OR REPLACE TABLE ds.t AS SELECT 1",
    "EXPORT DATA OPTIONS(uri='gs://b/x*.csv', format='CSV') AS SELECT 1",
    "DELETE FROM ds.t WHERE TRUE",
    "SELECT 1; DROP TABLE ds.t",
    "UPDATE ds.t SET a = 1 WHERE TRUE",
])
def test_bq_query_rejects_writes_before_any_api_call(gcp_config, sql):
    client, _ = make_bq_client()
    result = _run_bq(gcp_config, client, "bq_query", sql=sql)
    assert result.startswith("Error:")
    client.query.assert_not_called()


def test_bq_query_rejects_statement_bigquery_classifies_as_non_select(gcp_config):
    # Defence in depth: even if the lexical guard is fooled, BigQuery's own dry-run
    # classification (authoritative) must stop non-SELECT statements.
    client, job = make_bq_client(statement_type="SCRIPT")
    result = _run_bq(gcp_config, client, "bq_query", sql="SELECT 1")
    assert "classifies this statement as 'SCRIPT'" in result
    job.result.assert_not_called()


def test_bq_query_timeout_cancels_job(gcp_config):
    client, job = make_bq_client()
    job.result.side_effect = FutureTimeoutError()
    result = _run_bq(gcp_config, client, "bq_query", sql="SELECT 1")
    assert "did not finish" in result and "job-123" in result
    job.cancel.assert_called_once()


def test_bq_query_underlying_api_failure(gcp_config):
    client, _ = make_bq_client(query_side_effect=BadRequest("Syntax error near SELEKT"))
    result = _run_bq(gcp_config, client, "bq_query", sql="SELECT * FROM t WHERE x =")
    assert result.startswith("Error:") and "BadRequest" in result


def test_bq_query_unknown_env(gcp_config):
    gcp_config(bigquery_tools)
    tools = get_all_tools(bigquery_tools)
    result = tools["bq_query"](sql="SELECT 1", env="does-not-exist")
    assert "Unknown GCP environment" in result
    assert "does-not-exist" in result


# ---------------------------------------------------------------------------
# bq_preview_table: reads rows directly (free), never runs SELECT * (full scan)
# ---------------------------------------------------------------------------

def test_bq_preview_table_uses_list_rows_not_a_query(gcp_config):
    client = MagicMock()
    client.list_rows.return_value = [{"id": 1}, {"id": 2}]
    result = _run_bq(gcp_config, client, "bq_preview_table", dataset_id="part_catalog", table_id="part_level", limit=10)
    assert json.loads(result) == [{"id": 1}, {"id": 2}]
    client.list_rows.assert_called_once_with("test-project.part_catalog.part_level", max_results=10)
    client.query.assert_not_called()


def test_bq_preview_table_limit_clamped(gcp_config):
    client = MagicMock()
    client.list_rows.return_value = [{"id": 1}]
    _run_bq(gcp_config, client, "bq_preview_table", dataset_id="d", table_id="t", limit=500)
    assert client.list_rows.call_args.kwargs["max_results"] == 50


def test_bq_preview_table_no_rows(gcp_config):
    client = MagicMock()
    client.list_rows.return_value = []
    assert _run_bq(gcp_config, client, "bq_preview_table", dataset_id="d", table_id="t") == "Table has no rows."


@pytest.mark.parametrize("dataset,table", [("d`; DROP", "t"), ("d", "t` UNION SELECT"), ("d", "../x"), ("", "t")])
def test_bq_preview_table_rejects_unsafe_identifiers(gcp_config, dataset, table):
    client = MagicMock()
    result = _run_bq(gcp_config, client, "bq_preview_table", dataset_id=dataset, table_id=table)
    assert result.startswith("Error:") and "Invalid" in result
    client.list_rows.assert_not_called()


def test_bq_preview_table_unknown_env(gcp_config):
    gcp_config(bigquery_tools)
    tools = get_all_tools(bigquery_tools)
    result = tools["bq_preview_table"](dataset_id="d", table_id="t", env="does-not-exist")
    assert "Unknown GCP environment" in result


def test_bq_list_datasets_discloses_truncation(gcp_config):
    client = MagicMock()
    client.list_datasets.return_value = iter([SimpleNamespace(dataset_id=f"ds{i}") for i in range(250)])
    result = _run_bq(gcp_config, client, "bq_list_datasets")
    assert "ds199" in result and "ds200" not in result
    assert "more exist" in result
