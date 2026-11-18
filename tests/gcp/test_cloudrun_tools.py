import json

import pytest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from providers.gcp import cloudrun_tools
from tests.helpers import get_all_tools


def _service(name="my-svc", uri="https://my-svc-xyz-uc.a.run.app",
             latest_ready_revision="projects/test-project/locations/us-central1/services/my-svc/revisions/my-svc-00001-abc",
             update_time="2024-01-01T00:00:00Z", template=None):
    return SimpleNamespace(
        name=f"projects/test-project/locations/us-central1/services/{name}",
        uri=uri,
        latest_ready_revision=latest_ready_revision,
        update_time=update_time,
        template=template,
    )


def _template():
    container = SimpleNamespace(
        image="gcr.io/test-project/my-svc:latest",
        resources=SimpleNamespace(limits={"cpu": "1", "memory": "512Mi"}),
        env=[SimpleNamespace(name="ENV_VAR1"), SimpleNamespace(name="ENV_VAR2")],
    )
    return SimpleNamespace(
        containers=[container],
        scaling=SimpleNamespace(min_instance_count=1, max_instance_count=5),
    )


def _revision(idx=1):
    return SimpleNamespace(
        name=f"projects/test-project/locations/us-central1/services/my-svc/revisions/my-svc-0000{idx}-abc",
        create_time=f"2024-01-0{idx}T00:00:00Z",
        containers=[SimpleNamespace(image="gcr.io/test-project/my-svc:latest")],
    )


def _log_entry(payload, severity="INFO", timestamp="2024-01-01T00:00:00Z"):
    return SimpleNamespace(timestamp=timestamp, severity=severity, payload=payload)


# --------------------------------------------------------------------------
# cloudrun_list_services
# --------------------------------------------------------------------------

def test_cloudrun_list_services_happy(gcp_config):
    gcp_config(cloudrun_tools)
    with patch("providers.gcp.cloudrun_tools.run_v2.ServicesClient") as MockClient, \
         patch("providers.gcp.cloudrun_tools.get_credentials", return_value=MagicMock()):
        svc1 = _service(name="my-svc")
        svc2 = _service(name="other-svc", latest_ready_revision="")
        MockClient.return_value.list_services.return_value = [svc1, svc2]

        tools = get_all_tools(cloudrun_tools)
        result = tools["cloudrun_list_services"](env="test")
        data = json.loads(result)

        assert len(data) == 2
        assert data[0]["name"] == "my-svc"
        assert data[0]["url"] == "https://my-svc-xyz-uc.a.run.app"
        assert data[0]["latestRevision"] == "my-svc-00001-abc"
        assert data[1]["name"] == "other-svc"
        assert data[1]["latestRevision"] == ""

        MockClient.return_value.list_services.assert_called_once()
        _, kwargs = MockClient.return_value.list_services.call_args
        assert kwargs["parent"] == "projects/test-project/locations/us-central1"


def test_cloudrun_list_services_empty(gcp_config):
    gcp_config(cloudrun_tools)
    with patch("providers.gcp.cloudrun_tools.run_v2.ServicesClient") as MockClient, \
         patch("providers.gcp.cloudrun_tools.get_credentials", return_value=MagicMock()):
        MockClient.return_value.list_services.return_value = []

        tools = get_all_tools(cloudrun_tools)
        result = tools["cloudrun_list_services"](env="test")
        assert result == "No Cloud Run services found in region 'us-central1'."


def test_cloudrun_list_services_unknown_env(gcp_config):
    gcp_config(cloudrun_tools)
    with patch("providers.gcp.cloudrun_tools.run_v2.ServicesClient"), \
         patch("providers.gcp.cloudrun_tools.get_credentials", return_value=MagicMock()):
        tools = get_all_tools(cloudrun_tools)
        result = tools["cloudrun_list_services"](env="does-not-exist")
        assert "Unknown GCP environment" in result


# --------------------------------------------------------------------------
# cloudrun_describe_service
# --------------------------------------------------------------------------

def test_cloudrun_describe_service_happy(gcp_config):
    gcp_config(cloudrun_tools)
    with patch("providers.gcp.cloudrun_tools.run_v2.ServicesClient") as MockClient, \
         patch("providers.gcp.cloudrun_tools.get_credentials", return_value=MagicMock()):
        svc = _service(name="my-svc", template=_template())
        MockClient.return_value.get_service.return_value = svc

        tools = get_all_tools(cloudrun_tools)
        result = tools["cloudrun_describe_service"](service_name="my-svc", env="test")
        data = json.loads(result)

        assert data["name"] == "my-svc"
        assert data["url"] == "https://my-svc-xyz-uc.a.run.app"
        assert data["latestRevision"] == "my-svc-00001-abc"
        assert len(data["containers"]) == 1
        assert data["containers"][0]["image"] == "gcr.io/test-project/my-svc:latest"
        assert data["containers"][0]["resources"]["cpu"] == "1"
        assert data["containers"][0]["resources"]["memory"] == "512Mi"
        assert data["containers"][0]["env"] == ["ENV_VAR1", "ENV_VAR2"]
        assert data["scaling"]["minInstances"] == 1
        assert data["scaling"]["maxInstances"] == 5

        _, kwargs = MockClient.return_value.get_service.call_args
        assert kwargs["name"] == "projects/test-project/locations/us-central1/services/my-svc"


def test_cloudrun_describe_service_api_failure(gcp_config):
    gcp_config(cloudrun_tools)
    with patch("providers.gcp.cloudrun_tools.run_v2.ServicesClient") as MockClient, \
         patch("providers.gcp.cloudrun_tools.get_credentials", return_value=MagicMock()):
        MockClient.return_value.get_service.side_effect = RuntimeError("service not found")

        tools = get_all_tools(cloudrun_tools)
        result = tools["cloudrun_describe_service"](service_name="missing-svc", env="test")
        assert result.startswith("Error:") and "RuntimeError" in result
        assert "service not found" in result


def test_cloudrun_describe_service_unknown_env(gcp_config):
    gcp_config(cloudrun_tools)
    with patch("providers.gcp.cloudrun_tools.run_v2.ServicesClient"), \
         patch("providers.gcp.cloudrun_tools.get_credentials", return_value=MagicMock()):
        tools = get_all_tools(cloudrun_tools)
        result = tools["cloudrun_describe_service"](service_name="my-svc", env="does-not-exist")
        assert "Unknown GCP environment" in result


# --------------------------------------------------------------------------
# cloudrun_list_revisions
# --------------------------------------------------------------------------

def test_cloudrun_list_revisions_happy(gcp_config):
    gcp_config(cloudrun_tools)
    with patch("providers.gcp.cloudrun_tools.run_v2.RevisionsClient") as MockClient, \
         patch("providers.gcp.cloudrun_tools.get_credentials", return_value=MagicMock()):
        MockClient.return_value.list_revisions.return_value = [_revision(1), _revision(2)]

        tools = get_all_tools(cloudrun_tools)
        result = tools["cloudrun_list_revisions"](service_name="my-svc", env="test")
        data = json.loads(result)

        assert len(data) == 2
        assert data[0]["name"] == "my-svc-00001-abc"
        assert data[0]["containers"] == ["gcr.io/test-project/my-svc:latest"]

        _, kwargs = MockClient.return_value.list_revisions.call_args
        assert kwargs["parent"] == "projects/test-project/locations/us-central1/services/my-svc"


def test_cloudrun_list_revisions_empty(gcp_config):
    gcp_config(cloudrun_tools)
    with patch("providers.gcp.cloudrun_tools.run_v2.RevisionsClient") as MockClient, \
         patch("providers.gcp.cloudrun_tools.get_credentials", return_value=MagicMock()):
        MockClient.return_value.list_revisions.return_value = []

        tools = get_all_tools(cloudrun_tools)
        result = tools["cloudrun_list_revisions"](service_name="my-svc", env="test")
        assert result == "No revisions found for service 'my-svc'."


def test_cloudrun_list_revisions_limit_clamp(gcp_config):
    gcp_config(cloudrun_tools)
    with patch("providers.gcp.cloudrun_tools.run_v2.RevisionsClient") as MockClient, \
         patch("providers.gcp.cloudrun_tools.get_credentials", return_value=MagicMock()):
        MockClient.return_value.list_revisions.return_value = [_revision(i) for i in range(1, 10)] * 7  # 63 items

        tools = get_all_tools(cloudrun_tools)
        result = tools["cloudrun_list_revisions"](service_name="my-svc", limit=1000, env="test")
        body, note = result.split("\n[", 1)
        assert len(json.loads(body)) == 50
        assert "more exist" in note


def test_cloudrun_list_revisions_unknown_env(gcp_config):
    gcp_config(cloudrun_tools)
    with patch("providers.gcp.cloudrun_tools.run_v2.RevisionsClient"), \
         patch("providers.gcp.cloudrun_tools.get_credentials", return_value=MagicMock()):
        tools = get_all_tools(cloudrun_tools)
        result = tools["cloudrun_list_revisions"](service_name="my-svc", env="does-not-exist")
        assert "Unknown GCP environment" in result


# --------------------------------------------------------------------------
# cloudrun_get_logs
# --------------------------------------------------------------------------

def test_cloudrun_get_logs_str_payload(gcp_config):
    gcp_config(cloudrun_tools)
    with patch("providers.gcp.cloudrun_tools.gcp_logging.Client") as MockClient, \
         patch("providers.gcp.cloudrun_tools.get_credentials", return_value=MagicMock()):
        MockClient.return_value.list_entries.return_value = [
            _log_entry("plain text log line", severity="INFO"),
        ]

        tools = get_all_tools(cloudrun_tools)
        result = tools["cloudrun_get_logs"](service_name="my-svc", env="test")
        data = json.loads(result)

        assert len(data) == 1
        assert data[0]["severity"] == "INFO"
        assert data[0]["message"] == "plain text log line"


def test_cloudrun_get_logs_dict_payload(gcp_config):
    gcp_config(cloudrun_tools)
    with patch("providers.gcp.cloudrun_tools.gcp_logging.Client") as MockClient, \
         patch("providers.gcp.cloudrun_tools.get_credentials", return_value=MagicMock()):
        MockClient.return_value.list_entries.return_value = [
            _log_entry({"message": "structured entry", "code": 500}, severity="WARNING"),
        ]

        tools = get_all_tools(cloudrun_tools)
        result = tools["cloudrun_get_logs"](service_name="my-svc", env="test")
        data = json.loads(result)

        assert len(data) == 1
        assert data[0]["severity"] == "WARNING"
        parsed_message = json.loads(data[0]["message"])
        assert parsed_message == {"message": "structured entry", "code": 500}


def test_cloudrun_get_logs_empty(gcp_config):
    gcp_config(cloudrun_tools)
    with patch("providers.gcp.cloudrun_tools.gcp_logging.Client") as MockClient, \
         patch("providers.gcp.cloudrun_tools.get_credentials", return_value=MagicMock()):
        MockClient.return_value.list_entries.return_value = []

        tools = get_all_tools(cloudrun_tools)
        result = tools["cloudrun_get_logs"](service_name="my-svc", env="test")
        assert result == "No logs found for 'my-svc' (Cloud Run service or job) in the selected time window."


def test_cloudrun_get_logs_limit_clamp(gcp_config):
    gcp_config(cloudrun_tools)
    with patch("providers.gcp.cloudrun_tools.gcp_logging.Client") as MockClient, \
         patch("providers.gcp.cloudrun_tools.get_credentials", return_value=MagicMock()):
        MockClient.return_value.list_entries.return_value = []

        tools = get_all_tools(cloudrun_tools)
        tools["cloudrun_get_logs"](service_name="my-svc", limit=9999, env="test")

        _, kwargs = MockClient.return_value.list_entries.call_args
        assert kwargs["max_results"] == 200
        assert 'resource.labels.service_name="my-svc"' in kwargs["filter_"]
        assert "severity>=ERROR" not in kwargs["filter_"]


def test_cloudrun_get_logs_unknown_env(gcp_config):
    gcp_config(cloudrun_tools)
    with patch("providers.gcp.cloudrun_tools.gcp_logging.Client"), \
         patch("providers.gcp.cloudrun_tools.get_credentials", return_value=MagicMock()):
        tools = get_all_tools(cloudrun_tools)
        result = tools["cloudrun_get_logs"](service_name="my-svc", env="does-not-exist")
        assert "Unknown GCP environment" in result


def test_cloudrun_get_logs_api_failure(gcp_config):
    gcp_config(cloudrun_tools)
    with patch("providers.gcp.cloudrun_tools.gcp_logging.Client") as MockClient, \
         patch("providers.gcp.cloudrun_tools.get_credentials", return_value=MagicMock()):
        MockClient.return_value.list_entries.side_effect = RuntimeError("log backend unavailable")

        tools = get_all_tools(cloudrun_tools)
        result = tools["cloudrun_get_logs"](service_name="my-svc", env="test")
        assert result.startswith("Error:") and "log backend unavailable" in result


# --------------------------------------------------------------------------
# cloudrun_get_error_logs
# --------------------------------------------------------------------------

def test_cloudrun_get_error_logs_happy(gcp_config):
    gcp_config(cloudrun_tools)
    with patch("providers.gcp.cloudrun_tools.gcp_logging.Client") as MockClient, \
         patch("providers.gcp.cloudrun_tools.get_credentials", return_value=MagicMock()):
        MockClient.return_value.list_entries.return_value = [
            _log_entry("boom, it broke", severity="ERROR"),
        ]

        tools = get_all_tools(cloudrun_tools)
        result = tools["cloudrun_get_error_logs"](service_name="my-svc", env="test")
        data = json.loads(result)

        assert len(data) == 1
        assert data[0]["severity"] == "ERROR"
        assert data[0]["message"] == "boom, it broke"

        _, kwargs = MockClient.return_value.list_entries.call_args
        assert "severity>=ERROR" in kwargs["filter_"]


def test_cloudrun_get_error_logs_empty(gcp_config):
    gcp_config(cloudrun_tools)
    with patch("providers.gcp.cloudrun_tools.gcp_logging.Client") as MockClient, \
         patch("providers.gcp.cloudrun_tools.get_credentials", return_value=MagicMock()):
        MockClient.return_value.list_entries.return_value = []

        tools = get_all_tools(cloudrun_tools)
        result = tools["cloudrun_get_error_logs"](service_name="my-svc", env="test")
        assert result == "No error logs found for 'my-svc' (Cloud Run service or job) in the selected time window."


def test_cloudrun_get_error_logs_limit_clamp(gcp_config):
    gcp_config(cloudrun_tools)
    with patch("providers.gcp.cloudrun_tools.gcp_logging.Client") as MockClient, \
         patch("providers.gcp.cloudrun_tools.get_credentials", return_value=MagicMock()):
        MockClient.return_value.list_entries.return_value = []

        tools = get_all_tools(cloudrun_tools)
        tools["cloudrun_get_error_logs"](service_name="my-svc", limit=500, env="test")

        _, kwargs = MockClient.return_value.list_entries.call_args
        assert kwargs["max_results"] == 200


def test_cloudrun_get_error_logs_unknown_env(gcp_config):
    gcp_config(cloudrun_tools)
    with patch("providers.gcp.cloudrun_tools.gcp_logging.Client"), \
         patch("providers.gcp.cloudrun_tools.get_credentials", return_value=MagicMock()):
        tools = get_all_tools(cloudrun_tools)
        result = tools["cloudrun_get_error_logs"](service_name="my-svc", env="does-not-exist")
        assert "Unknown GCP environment" in result


# --------------------------------------------------------------------------
# regions, jobs, time window, filter safety
# --------------------------------------------------------------------------

def _logs_call(gcp_config, tool="cloudrun_get_logs", **kwargs):
    gcp_config(cloudrun_tools)
    with patch("providers.gcp.cloudrun_tools.gcp_logging.Client") as MockClient, \
         patch("providers.gcp.cloudrun_tools.get_credentials", return_value=MagicMock()):
        MockClient.return_value.list_entries.return_value = []
        result = get_all_tools(cloudrun_tools)[tool](env="test", **kwargs)
        call = MockClient.return_value.list_entries.call_args
        return result, (call.kwargs.get("filter_") if call else None)


def test_list_services_honours_region_argument(gcp_config):
    gcp_config(cloudrun_tools)
    with patch("providers.gcp.cloudrun_tools.run_v2.ServicesClient") as MockClient, \
         patch("providers.gcp.cloudrun_tools.get_credentials", return_value=MagicMock()):
        MockClient.return_value.list_services.return_value = []
        result = get_all_tools(cloudrun_tools)["cloudrun_list_services"](region="europe-west1", env="test")
    assert MockClient.return_value.list_services.call_args.kwargs["parent"] == "projects/test-project/locations/europe-west1"
    assert "europe-west1" in result


def test_describe_service_honours_region_argument(gcp_config):
    gcp_config(cloudrun_tools)
    with patch("providers.gcp.cloudrun_tools.run_v2.ServicesClient") as MockClient, \
         patch("providers.gcp.cloudrun_tools.get_credentials", return_value=MagicMock()):
        MockClient.return_value.get_service.side_effect = RuntimeError("stop here")
        get_all_tools(cloudrun_tools)["cloudrun_describe_service"](service_name="svc", region="asia-east1", env="test")
    assert MockClient.return_value.get_service.call_args.kwargs["name"] == \
        "projects/test-project/locations/asia-east1/services/svc"


def test_list_revisions_honours_region_argument(gcp_config):
    gcp_config(cloudrun_tools)
    with patch("providers.gcp.cloudrun_tools.run_v2.RevisionsClient") as MockClient, \
         patch("providers.gcp.cloudrun_tools.get_credentials", return_value=MagicMock()):
        MockClient.return_value.list_revisions.return_value = []
        get_all_tools(cloudrun_tools)["cloudrun_list_revisions"](service_name="svc", region="asia-east1", env="test")
    assert MockClient.return_value.list_revisions.call_args.kwargs["parent"] == \
        "projects/test-project/locations/asia-east1/services/svc"


def test_log_filter_covers_cloud_run_jobs_as_well_as_services(gcp_config):
    _, flt = _logs_call(gcp_config, service_name="nightly-report")
    assert 'resource.type="cloud_run_revision" AND resource.labels.service_name="nightly-report"' in flt
    assert 'resource.type="cloud_run_job" AND resource.labels.job_name="nightly-report"' in flt


def test_error_filter_is_and_ed_onto_the_whole_service_or_job_group(gcp_config):
    _, flt = _logs_call(gcp_config, "cloudrun_get_error_logs", service_name="svc")
    # severity must apply to BOTH alternatives: the OR group has to be fully parenthesised
    assert flt.startswith("((") and "))" in flt.split("AND timestamp")[0]
    assert flt.endswith("AND severity>=ERROR")


def test_log_filter_has_default_one_hour_window(gcp_config):
    import re
    from datetime import datetime, timedelta, timezone
    _, flt = _logs_call(gcp_config, service_name="svc")
    ts = datetime.strptime(re.search(r'timestamp>="([^"]+)"', flt).group(1), "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    assert timedelta(minutes=59) < datetime.now(timezone.utc) - ts < timedelta(minutes=61)
    assert "timestamp<=" not in flt


def test_log_filter_until_minutes_adds_upper_bound(gcp_config):
    _, flt = _logs_call(gcp_config, service_name="svc", since_minutes=120, until_minutes=30)
    assert 'timestamp>="' in flt and 'timestamp<="' in flt


@pytest.mark.parametrize("since,until", [(0, 0), (10, 10), (10, 20), (60 * 24 * 31, 0)])
def test_invalid_log_window_is_an_error_and_makes_no_api_call(gcp_config, since, until):
    gcp_config(cloudrun_tools)
    with patch("providers.gcp.cloudrun_tools.gcp_logging.Client") as MockClient, \
         patch("providers.gcp.cloudrun_tools.get_credentials", return_value=MagicMock()):
        result = get_all_tools(cloudrun_tools)["cloudrun_get_logs"](
            service_name="svc", since_minutes=since, until_minutes=until, env="test")
    assert result.startswith("Error:")
    MockClient.return_value.list_entries.assert_not_called()


def test_service_name_cannot_break_out_of_the_log_filter(gcp_config):
    _, flt = _logs_call(gcp_config, service_name='x" OR severity>=DEBUG OR "a"="a')
    assert 'resource.labels.service_name="x\\" OR severity>=DEBUG OR \\"a\\"=\\"a"' in flt


def test_service_name_with_newline_is_rejected(gcp_config):
    result, flt = _logs_call(gcp_config, service_name="svc\nOR TRUE")
    assert result.startswith("Error:") and "Control characters" in result
    assert flt is None
