"""Tests for providers/gcp/cloudbuild_tools.py.

Cloud Build has two build collections: global (projects/{p}/builds) and one per region
(projects/{p}/locations/{r}/builds). A fake HTTP layer routes by URL, so these tests can
prove both are consulted (the original code only ever queried the regional one)."""
import json
from unittest.mock import MagicMock, patch

import pytest

from providers.gcp import cloudbuild_tools
from tests.helpers import get_all_tools

GLOBAL = "https://cloudbuild.googleapis.com/v1/projects/test-project/builds"
REGIONAL = "https://cloudbuild.googleapis.com/v1/projects/test-project/locations/us-central1/builds"


def _resp(json_data, status=200, error=None):
    resp = MagicMock()
    resp.status_code = status
    resp.json.return_value = json_data
    if error:
        resp.raise_for_status.side_effect = error
    elif status >= 400:
        resp.raise_for_status.side_effect = Exception(f"HTTP {status}")
    else:
        resp.raise_for_status.return_value = None
    return resp


class FakeApi:
    """Routes requests.get by URL. Unlisted URLs return 404."""

    def __init__(self, routes):
        self.routes = routes
        self.calls = []

    def __call__(self, url, headers=None, params=None, timeout=None):
        self.calls.append({"url": url, "params": params})
        if url in self.routes:
            return self.routes[url]
        return _resp({"error": "not found"}, status=404)

    def urls(self):
        return [c["url"] for c in self.calls]


def _patch_auth():
    return patch("providers.gcp.cloudbuild_tools.get_access_token", return_value="fake-token")


def _run(gcp_config, routes, tool, **kwargs):
    gcp_config(cloudbuild_tools)
    api = FakeApi(routes)
    with _patch_auth(), patch("requests.get", side_effect=api):
        result = get_all_tools(cloudbuild_tools)[tool](env="test", **kwargs)
    return result, api


BUILD_1 = {
    "id": "abc123", "status": "SUCCESS",
    "createTime": "2026-01-01T00:00:00Z", "finishTime": "2026-01-01T00:05:00Z",
    "logUrl": "https://console.cloud.google.com/logs/abc123", "logsBucket": "gs://my-log-bucket",
    "substitutions": {"BRANCH_NAME": "main"},
    "steps": [{"id": "step1", "name": "gcr.io/cloud-builders/docker", "status": "SUCCESS", "timing": {}}],
}
BUILD_2 = {
    "id": "def456", "status": "FAILURE",
    "createTime": "2026-01-02T00:00:00Z", "finishTime": "2026-01-02T00:05:00Z",
    "logUrl": "https://console.cloud.google.com/logs/def456", "logsBucket": "gs://my-log-bucket",
    "substitutions": {"BRANCH_NAME": "dev"}, "steps": [],
}


def body(result):
    return json.loads(result.split("\n[Searched")[0])


# ---------------------------------------------------------------------------
# list
# ---------------------------------------------------------------------------

def test_list_builds_merges_global_and_regional_newest_first(gcp_config):
    result, api = _run(gcp_config, {
        GLOBAL: _resp({"builds": [BUILD_1]}),
        REGIONAL: _resp({"builds": [BUILD_2]}),
    }, "cloudbuild_list_builds")
    assert set(api.urls()) == {GLOBAL, REGIONAL}
    parsed = body(result)
    assert [b["id"] for b in parsed] == ["def456", "abc123"]  # newest first
    assert parsed[1]["branch"] == "main" and parsed[1]["logUrl"].endswith("abc123")
    assert "Searched: global, us-central1" in result


def test_list_builds_finds_global_only_builds_when_regional_is_empty(gcp_config):
    # The reported bug: builds from global triggers never appeared.
    result, _ = _run(gcp_config, {GLOBAL: _resp({"builds": [BUILD_1]}), REGIONAL: _resp({})}, "cloudbuild_list_builds")
    assert [b["id"] for b in body(result)] == ["abc123"]


def test_list_builds_dedupes_and_applies_limit(gcp_config):
    result, _ = _run(gcp_config, {
        GLOBAL: _resp({"builds": [BUILD_1, BUILD_2]}),
        REGIONAL: _resp({"builds": [BUILD_2]}),
    }, "cloudbuild_list_builds", limit=1)
    assert [b["id"] for b in body(result)] == ["def456"]


def test_list_builds_region_global_queries_only_global(gcp_config):
    _, api = _run(gcp_config, {GLOBAL: _resp({"builds": [BUILD_1]})}, "cloudbuild_list_builds", region="global")
    assert api.urls() == [GLOBAL]


def test_list_builds_custom_region(gcp_config):
    eu = "https://cloudbuild.googleapis.com/v1/projects/test-project/locations/europe-west1/builds"
    result, api = _run(gcp_config, {eu: _resp({"builds": [BUILD_2]})}, "cloudbuild_list_builds", region="europe-west1")
    assert eu in api.urls() and "def456" in result


def test_list_builds_empty(gcp_config):
    result, _ = _run(gcp_config, {GLOBAL: _resp({"builds": []}), REGIONAL: _resp({"builds": []})}, "cloudbuild_list_builds")
    assert result.startswith("No builds found.")
    assert "Searched: global, us-central1" in result


def test_list_builds_one_scope_failing_is_reported_not_hidden(gcp_config):
    result, _ = _run(gcp_config, {
        GLOBAL: _resp({"builds": [BUILD_1]}),
        REGIONAL: _resp({}, status=403),
    }, "cloudbuild_list_builds")
    assert "abc123" in result
    assert "Failed: us-central1" in result


def test_list_builds_all_scopes_failing_is_an_error(gcp_config):
    result, _ = _run(gcp_config, {GLOBAL: _resp({}, status=403), REGIONAL: _resp({}, status=403)}, "cloudbuild_list_builds")
    assert result.startswith("Error:") and "Could not list builds" in result


def test_list_builds_limit_clamped(gcp_config):
    _, api = _run(gcp_config, {GLOBAL: _resp({"builds": []}), REGIONAL: _resp({"builds": []})},
                  "cloudbuild_list_builds", limit=500)
    assert all(c["params"]["pageSize"] == 50 for c in api.calls)


def test_list_builds_branch_filter_passed(gcp_config):
    _, api = _run(gcp_config, {GLOBAL: _resp({"builds": []}), REGIONAL: _resp({"builds": []})},
                  "cloudbuild_list_builds", branch="dev")
    assert all(c["params"]["filter"] == 'substitutions.BRANCH_NAME="dev"' for c in api.calls)


def test_list_builds_branch_filter_cannot_be_injected(gcp_config):
    _, api = _run(gcp_config, {GLOBAL: _resp({"builds": []}), REGIONAL: _resp({"builds": []})},
                  "cloudbuild_list_builds", branch='x" OR status="FAILURE')
    assert api.calls[0]["params"]["filter"] == 'substitutions.BRANCH_NAME="x\\" OR status=\\"FAILURE"'


def test_list_builds_unknown_environment(gcp_config):
    gcp_config(cloudbuild_tools)
    result = get_all_tools(cloudbuild_tools)["cloudbuild_list_builds"](env="does-not-exist")
    assert "Unknown GCP environment" in result and "does-not-exist" in result


# ---------------------------------------------------------------------------
# describe
# ---------------------------------------------------------------------------

def test_describe_build_finds_a_global_build(gcp_config):
    result, api = _run(gcp_config, {f"{GLOBAL}/abc123": _resp(BUILD_1)}, "cloudbuild_describe_build", build_id="abc123")
    parsed = json.loads(result)
    assert parsed["id"] == "abc123" and parsed["status"] == "SUCCESS"
    assert parsed["substitutions"] == {"BRANCH_NAME": "main"}
    assert parsed["steps"][0]["name"] == "gcr.io/cloud-builders/docker"


def test_describe_build_falls_back_to_regional_when_global_404s(gcp_config):
    result, api = _run(gcp_config, {f"{REGIONAL}/def456": _resp(BUILD_2)}, "cloudbuild_describe_build", build_id="def456")
    assert json.loads(result)["status"] == "FAILURE"
    assert api.urls() == [f"{GLOBAL}/def456", f"{REGIONAL}/def456"]


def test_describe_build_not_found_anywhere_says_where_it_looked(gcp_config):
    result, _ = _run(gcp_config, {}, "cloudbuild_describe_build", build_id="nope")
    assert "Build 'nope' not found (searched: global, us-central1)" in result
    assert "region=" in result


def test_describe_build_non_404_error_propagates(gcp_config):
    result, _ = _run(gcp_config, {f"{GLOBAL}/abc123": _resp({}, status=500)}, "cloudbuild_describe_build", build_id="abc123")
    assert result.startswith("Error:") and "HTTP 500" in result


def test_describe_build_unknown_environment(gcp_config):
    gcp_config(cloudbuild_tools)
    result = get_all_tools(cloudbuild_tools)["cloudbuild_describe_build"](build_id="x", env="does-not-exist")
    assert "Unknown GCP environment" in result


# ---------------------------------------------------------------------------
# logs
# ---------------------------------------------------------------------------

def _gcs(blob_size, content=b"log text"):
    blob = MagicMock()
    blob.size = blob_size
    blob.download_as_bytes.return_value = content
    bucket = MagicMock()
    bucket.blob.return_value = blob
    client = MagicMock()
    client.bucket.return_value = bucket
    return client, bucket, blob


def test_get_logs_small_log_returned_whole(gcp_config):
    client, bucket, blob = _gcs(100, b"Step 1/1: building...\nBuild successful.")
    gcp_config(cloudbuild_tools)
    with _patch_auth(), patch("requests.get", side_effect=FakeApi({f"{GLOBAL}/abc123": _resp(BUILD_1)})), \
            patch("providers.gcp.auth.get_credentials", return_value=MagicMock()), \
            patch("google.cloud.storage.Client", return_value=client):
        result = get_all_tools(cloudbuild_tools)["cloudbuild_get_logs"](build_id="abc123", env="test")
    assert result == "Step 1/1: building...\nBuild successful."
    bucket.blob.assert_called_once_with("log-abc123.txt")
    client.bucket.assert_called_once_with("my-log-bucket")
    blob.download_as_bytes.assert_called_once_with(start=0)


def test_get_logs_large_log_is_tailed_not_fully_downloaded(gcp_config):
    size = 50_000_000
    client, _, blob = _gcs(size, b"...last lines")
    gcp_config(cloudbuild_tools)
    with _patch_auth(), patch("requests.get", side_effect=FakeApi({f"{GLOBAL}/abc123": _resp(BUILD_1)})), \
            patch("providers.gcp.auth.get_credentials", return_value=MagicMock()), \
            patch("google.cloud.storage.Client", return_value=client):
        result = get_all_tools(cloudbuild_tools)["cloudbuild_get_logs"](build_id="abc123", env="test")
    blob.download_as_bytes.assert_called_once_with(start=size - cloudbuild_tools.MAX_LOG_BYTES)
    assert result.startswith("[Log is 50,000,000 bytes; showing the last 200,000.]")
    assert result.endswith("...last lines")


def test_get_logs_gcs_failure_is_an_error_with_the_log_url(gcp_config):
    gcp_config(cloudbuild_tools)
    client = MagicMock()
    client.bucket.side_effect = Exception("bucket not found")
    with _patch_auth(), patch("requests.get", side_effect=FakeApi({f"{GLOBAL}/abc123": _resp(BUILD_1)})), \
            patch("providers.gcp.auth.get_credentials", return_value=MagicMock()), \
            patch("google.cloud.storage.Client", return_value=client):
        result = get_all_tools(cloudbuild_tools)["cloudbuild_get_logs"](build_id="abc123", env="test")
    assert result.startswith("Error:")  # not a successful result any more
    assert "bucket not found" in result and "https://console.cloud.google.com/logs/abc123" in result


def test_get_logs_regional_build(gcp_config):
    client, _, _ = _gcs(10, b"ok")
    gcp_config(cloudbuild_tools)
    with _patch_auth(), patch("requests.get", side_effect=FakeApi({f"{REGIONAL}/def456": _resp(BUILD_2)})), \
            patch("providers.gcp.auth.get_credentials", return_value=MagicMock()), \
            patch("google.cloud.storage.Client", return_value=client):
        assert get_all_tools(cloudbuild_tools)["cloudbuild_get_logs"](build_id="def456", env="test") == "ok"


def test_get_logs_unknown_environment(gcp_config):
    gcp_config(cloudbuild_tools)
    result = get_all_tools(cloudbuild_tools)["cloudbuild_get_logs"](build_id="abc123", env="does-not-exist")
    assert "Unknown GCP environment" in result


def test_get_logs_api_failure(gcp_config):
    result, _ = _run(gcp_config, {f"{GLOBAL}/abc123": _resp({}, status=500)}, "cloudbuild_get_logs", build_id="abc123")
    assert result.startswith("Error:")
