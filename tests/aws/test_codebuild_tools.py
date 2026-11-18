import json
import time
from unittest.mock import MagicMock, patch

import boto3
from moto import mock_aws

from providers.aws import codebuild_tools
from tests.helpers import get_all_tools, mock_paginators


# ---------------------------------------------------------------------------
# codebuild_list_projects: moto's CodeBuild create_project/list_projects works
# reliably, so this uses real moto end to end.
# ---------------------------------------------------------------------------

@mock_aws
def test_codebuild_list_projects_empty(aws_config):
    aws_config(codebuild_tools)
    tools = get_all_tools(codebuild_tools)
    result = tools["codebuild_list_projects"](env="test")
    assert result == "No CodeBuild projects found."


@mock_aws
def test_codebuild_list_projects_with_data(aws_config):
    aws_config(codebuild_tools)
    cb = boto3.client("codebuild", region_name="us-east-1")
    cb.create_project(
        name="my-project",
        source={"type": "GITHUB", "location": "https://github.com/example/repo.git"},
        artifacts={"type": "NO_ARTIFACTS"},
        environment={
            "type": "LINUX_CONTAINER",
            "image": "aws/codebuild/standard:5.0",
            "computeType": "BUILD_GENERAL1_SMALL",
        },
        serviceRole="arn:aws:iam::123456789012:role/service-role/my-role",
    )
    cb.create_project(
        name="another-project",
        source={"type": "GITHUB", "location": "https://github.com/example/other.git"},
        artifacts={"type": "NO_ARTIFACTS"},
        environment={
            "type": "LINUX_CONTAINER",
            "image": "aws/codebuild/standard:5.0",
            "computeType": "BUILD_GENERAL1_SMALL",
        },
        serviceRole="arn:aws:iam::123456789012:role/service-role/my-role",
    )

    tools = get_all_tools(codebuild_tools)
    result = tools["codebuild_list_projects"](env="test")
    assert "my-project" in result
    assert "another-project" in result


def test_codebuild_list_projects_unknown_env(aws_config):
    aws_config(codebuild_tools)
    tools = get_all_tools(codebuild_tools)
    result = tools["codebuild_list_projects"](env="does-not-exist")
    assert "Unknown AWS environment" in result
    assert "does-not-exist" in result


# ---------------------------------------------------------------------------
# codebuild_list_builds: moto's list_builds_for_project/batch_get_builds do not
# simulate real build execution (both return empty even after create_project),
# so this falls back to a mocked get_client returning realistic fake responses.
# ---------------------------------------------------------------------------

def test_codebuild_list_builds_with_data(aws_config):
    aws_config(codebuild_tools)
    mock_cb = MagicMock()
    mock_paginators(mock_cb, list_builds_for_project=[{
        "ids": ["my-project:aaaa-1111", "my-project:bbbb-2222", "my-project:cccc-3333"]
    }])
    mock_cb.batch_get_builds.return_value = {
        "builds": [
            {
                "id": "my-project:aaaa-1111",
                "buildStatus": "SUCCEEDED",
                "sourceVersion": "main",
                "startTime": "2026-01-01T00:00:00Z",
                "endTime": "2026-01-01T00:05:00Z",
            },
            {
                "id": "my-project:bbbb-2222",
                "buildStatus": "FAILED",
                "sourceVersion": "main",
                "startTime": "2026-01-02T00:00:00Z",
                "endTime": "2026-01-02T00:05:00Z",
            },
        ]
    }

    with patch("providers.aws.codebuild_tools.get_client", return_value=mock_cb):
        tools = get_all_tools(codebuild_tools)
        result = tools["codebuild_list_builds"](project_name="my-project", limit=2, env="test")

    body, note = result.split("\n[", 1)   # 3 builds exist, limit=2: the cut-off is disclosed
    assert "more exist" in note
    parsed = json.loads(body)
    assert len(parsed) == 2
    assert parsed[0]["id"] == "my-project:aaaa-1111"
    assert parsed[0]["status"] == "SUCCEEDED"
    assert parsed[1]["status"] == "FAILED"
    # limit=2 should have been applied to the ids slice before batch_get_builds was called
    (call,) = mock_cb.paginate_calls["list_builds_for_project"]
    assert call["projectName"] == "my-project"
    assert call["PaginationConfig"] == {"MaxItems": 3}   # limit + 1, so truncation can be detected
    mock_cb.batch_get_builds.assert_called_once_with(ids=["my-project:aaaa-1111", "my-project:bbbb-2222"])


def test_codebuild_list_builds_empty(aws_config):
    aws_config(codebuild_tools)
    mock_cb = MagicMock()
    mock_paginators(mock_cb, list_builds_for_project=[{"ids": []}])

    with patch("providers.aws.codebuild_tools.get_client", return_value=mock_cb):
        tools = get_all_tools(codebuild_tools)
        result = tools["codebuild_list_builds"](project_name="empty-project", env="test")

    assert result == "No builds found for project 'empty-project'."
    mock_cb.batch_get_builds.assert_not_called()


def test_codebuild_list_builds_limit_clamped(aws_config):
    aws_config(codebuild_tools)
    all_ids = [f"my-project:id-{i}" for i in range(60)]
    mock_cb = MagicMock()
    mock_paginators(mock_cb, list_builds_for_project=[{"ids": all_ids}])

    captured = {}

    def fake_batch_get_builds(ids):
        captured["ids"] = ids
        return {"builds": [{"id": i, "buildStatus": "SUCCEEDED"} for i in ids]}

    mock_cb.batch_get_builds.side_effect = fake_batch_get_builds

    with patch("providers.aws.codebuild_tools.get_client", return_value=mock_cb):
        tools = get_all_tools(codebuild_tools)
        result = tools["codebuild_list_builds"](project_name="my-project", limit=999, env="test")

    body, note = result.split("\n[", 1)
    parsed = json.loads(body)
    # limit=999 must be clamped to 50, and the cut-off must be disclosed
    assert len(parsed) == 50
    assert len(captured["ids"]) == 50
    assert "more exist" in note


def test_codebuild_list_builds_unknown_env(aws_config):
    aws_config(codebuild_tools)
    tools = get_all_tools(codebuild_tools)
    result = tools["codebuild_list_builds"](project_name="my-project", env="does-not-exist")
    assert "Unknown AWS environment" in result


# ---------------------------------------------------------------------------
# codebuild_get_logs
# ---------------------------------------------------------------------------

def test_codebuild_get_logs_build_not_found(aws_config):
    aws_config(codebuild_tools)
    mock_cb = MagicMock()
    mock_cb.batch_get_builds.return_value = {"builds": []}

    with patch("providers.aws.codebuild_tools.get_client", return_value=mock_cb):
        tools = get_all_tools(codebuild_tools)
        result = tools["codebuild_get_logs"](build_id="my-project:nope", env="test")

    assert result == "Build 'my-project:nope' not found."


def test_codebuild_get_logs_no_group_name(aws_config):
    aws_config(codebuild_tools)
    mock_cb = MagicMock()
    mock_cb.batch_get_builds.return_value = {
        "builds": [
            {
                "id": "my-project:abcd-1234",
                "logs": {"deepLink": "https://console.aws.amazon.com/some-deep-link"},
            }
        ]
    }

    with patch("providers.aws.codebuild_tools.get_client", return_value=mock_cb):
        tools = get_all_tools(codebuild_tools)
        result = tools["codebuild_get_logs"](build_id="my-project:abcd-1234", env="test")

    assert "No CloudWatch log group recorded" in result
    assert "https://console.aws.amazon.com/some-deep-link" in result


def test_codebuild_get_logs_with_stream_name(aws_config):
    aws_config(codebuild_tools)
    mock_cb = MagicMock()
    mock_cb.batch_get_builds.return_value = {
        "builds": [
            {
                "id": "my-project:abcd-1234",
                "logs": {
                    "groupName": "/aws/codebuild/my-project",
                    "streamName": "abcd-1234",
                },
            }
        ]
    }
    mock_logs = MagicMock()
    mock_logs.get_log_events.return_value = {
        "events": [
            {"message": "Build started\n"},
            {"message": "Running install phase\n"},
            {"message": "Build succeeded\n"},
        ]
    }

    def fake_get_client(aws_env, service_name):
        return mock_cb if service_name == "codebuild" else mock_logs

    with patch("providers.aws.codebuild_tools.get_client", side_effect=fake_get_client):
        tools = get_all_tools(codebuild_tools)
        result = tools["codebuild_get_logs"](build_id="my-project:abcd-1234", env="test")

    assert result == "Build started\nRunning install phase\nBuild succeeded"
    mock_logs.get_log_events.assert_called_once_with(
        logGroupName="/aws/codebuild/my-project", logStreamName="abcd-1234", limit=200
    )


def test_codebuild_get_logs_with_stream_name_no_events(aws_config):
    aws_config(codebuild_tools)
    mock_cb = MagicMock()
    mock_cb.batch_get_builds.return_value = {
        "builds": [
            {
                "id": "my-project:abcd-1234",
                "logs": {
                    "groupName": "/aws/codebuild/my-project",
                    "streamName": "abcd-1234",
                },
            }
        ]
    }
    mock_logs = MagicMock()
    mock_logs.get_log_events.return_value = {"events": []}

    def fake_get_client(aws_env, service_name):
        return mock_cb if service_name == "codebuild" else mock_logs

    with patch("providers.aws.codebuild_tools.get_client", side_effect=fake_get_client):
        tools = get_all_tools(codebuild_tools)
        result = tools["codebuild_get_logs"](build_id="my-project:abcd-1234", env="test")

    assert result == "No log events found for build 'my-project:abcd-1234'."


@mock_aws
def test_codebuild_get_logs_falls_back_to_fetch_log_events(aws_config):
    """When there's no streamName, the source falls through to fetch_log_events,
    which we let run for real against a moto-backed CloudWatch Logs group."""
    aws_config(codebuild_tools)

    logs = boto3.client("logs", region_name="us-east-1")
    logs.create_log_group(logGroupName="/aws/codebuild/my-project")
    logs.create_log_stream(logGroupName="/aws/codebuild/my-project", logStreamName="some-stream")
    now_ms = int(time.time() * 1000)
    logs.put_log_events(
        logGroupName="/aws/codebuild/my-project",
        logStreamName="some-stream",
        logEvents=[
            {"timestamp": now_ms, "message": "line one"},
            {"timestamp": now_ms + 1, "message": "line two"},
        ],
    )

    mock_cb = MagicMock()
    mock_cb.batch_get_builds.return_value = {
        "builds": [
            {
                "id": "my-project:abcd-1234",
                "logs": {"groupName": "/aws/codebuild/my-project"},
            }
        ]
    }

    def fake_get_client(aws_env, service_name):
        return mock_cb if service_name == "codebuild" else logs

    with patch("providers.aws.codebuild_tools.get_client", side_effect=fake_get_client):
        tools = get_all_tools(codebuild_tools)
        result = tools["codebuild_get_logs"](build_id="my-project:abcd-1234", env="test")

    parsed = json.loads(result)
    messages = [row["message"] for row in parsed]
    assert "line one" in messages
    assert "line two" in messages


@mock_aws
def test_codebuild_get_logs_falls_back_no_log_group(aws_config):
    """fetch_log_events catches ResourceNotFoundException when the log group doesn't exist."""
    aws_config(codebuild_tools)
    logs = boto3.client("logs", region_name="us-east-1")

    mock_cb = MagicMock()
    mock_cb.batch_get_builds.return_value = {
        "builds": [
            {
                "id": "my-project:abcd-1234",
                "logs": {"groupName": "/aws/codebuild/nonexistent"},
            }
        ]
    }

    def fake_get_client(aws_env, service_name):
        return mock_cb if service_name == "codebuild" else logs

    with patch("providers.aws.codebuild_tools.get_client", side_effect=fake_get_client):
        tools = get_all_tools(codebuild_tools)
        result = tools["codebuild_get_logs"](build_id="my-project:abcd-1234", env="test")

    assert "does not exist" in result


def test_codebuild_get_logs_unknown_env(aws_config):
    aws_config(codebuild_tools)
    tools = get_all_tools(codebuild_tools)
    result = tools["codebuild_get_logs"](build_id="my-project:abcd-1234", env="does-not-exist")
    assert "Unknown AWS environment" in result


# ---------------------------------------------------------------------------
# codebuild_describe_build
# ---------------------------------------------------------------------------

def test_codebuild_describe_build_not_found(aws_config):
    aws_config(codebuild_tools)
    mock_cb = MagicMock()
    mock_cb.batch_get_builds.return_value = {"builds": []}

    with patch("providers.aws.codebuild_tools.get_client", return_value=mock_cb):
        tools = get_all_tools(codebuild_tools)
        result = tools["codebuild_describe_build"](build_id="my-project:nope", env="test")

    assert result == "Build 'my-project:nope' not found."


def test_codebuild_describe_build_success(aws_config):
    aws_config(codebuild_tools)
    mock_cb = MagicMock()
    mock_cb.batch_get_builds.return_value = {
        "builds": [
            {
                "id": "my-project:abcd-1234",
                "buildStatus": "SUCCEEDED",
                "startTime": "2026-01-01T00:00:00Z",
                "endTime": "2026-01-01T00:05:00Z",
                "sourceVersion": "main",
                "phases": [
                    {"phaseType": "SUBMITTED", "phaseStatus": "SUCCEEDED", "durationInSeconds": 1},
                    {"phaseType": "BUILD", "phaseStatus": "SUCCEEDED", "durationInSeconds": 42},
                ],
            }
        ]
    }

    with patch("providers.aws.codebuild_tools.get_client", return_value=mock_cb):
        tools = get_all_tools(codebuild_tools)
        result = tools["codebuild_describe_build"](build_id="my-project:abcd-1234", env="test")

    parsed = json.loads(result)
    assert parsed["id"] == "my-project:abcd-1234"
    assert parsed["status"] == "SUCCEEDED"
    assert parsed["sourceVersion"] == "main"
    assert len(parsed["phases"]) == 2
    assert parsed["phases"][1] == {"phaseType": "BUILD", "phaseStatus": "SUCCEEDED", "durationInSeconds": 42}


def test_codebuild_describe_build_no_phases(aws_config):
    aws_config(codebuild_tools)
    mock_cb = MagicMock()
    mock_cb.batch_get_builds.return_value = {
        "builds": [
            {
                "id": "my-project:abcd-1234",
                "buildStatus": "IN_PROGRESS",
                "startTime": "2026-01-01T00:00:00Z",
                "endTime": None,
                "sourceVersion": "main",
            }
        ]
    }

    with patch("providers.aws.codebuild_tools.get_client", return_value=mock_cb):
        tools = get_all_tools(codebuild_tools)
        result = tools["codebuild_describe_build"](build_id="my-project:abcd-1234", env="test")

    parsed = json.loads(result)
    assert parsed["status"] == "IN_PROGRESS"
    assert parsed["phases"] == []


def test_codebuild_describe_build_unknown_env(aws_config):
    aws_config(codebuild_tools)
    tools = get_all_tools(codebuild_tools)
    result = tools["codebuild_describe_build"](build_id="my-project:abcd-1234", env="does-not-exist")
    assert "Unknown AWS environment" in result
