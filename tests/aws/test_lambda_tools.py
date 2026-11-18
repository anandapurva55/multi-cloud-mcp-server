import io
import json
import time
import zipfile
from unittest.mock import MagicMock, patch

import boto3
from moto import mock_aws

from providers.aws import lambda_tools
from tests.helpers import get_all_tools

ROLE_ARN = "arn:aws:iam::123456789012:role/lambda-role"


def _zip_bytes() -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("lambda_function.py", "def handler(event, context):\n    return event\n")
    return buf.getvalue()


def _create_role(iam):
    iam.create_role(RoleName="lambda-role", AssumeRolePolicyDocument="{}")


def _create_function(lam, name="my-function", **extra):
    kwargs = dict(
        FunctionName=name,
        Runtime="python3.12",
        Role=ROLE_ARN,
        Handler="lambda_function.handler",
        Code={"ZipFile": _zip_bytes()},
    )
    kwargs.update(extra)
    return lam.create_function(**kwargs)


# ---------------------------------------------------------------------------
# lambda_list_functions
# ---------------------------------------------------------------------------

@mock_aws
def test_lambda_list_functions_empty(aws_config):
    aws_config(lambda_tools)
    tools = get_all_tools(lambda_tools)
    result = tools["lambda_list_functions"](env="test")
    assert result == "No Lambda functions found."


@mock_aws
def test_lambda_list_functions_with_data(aws_config):
    aws_config(lambda_tools)
    iam = boto3.client("iam", region_name="us-east-1")
    _create_role(iam)
    lam = boto3.client("lambda", region_name="us-east-1")
    _create_function(lam, name="fn-one")
    _create_function(lam, name="fn-two")

    tools = get_all_tools(lambda_tools)
    result = tools["lambda_list_functions"](env="test")
    parsed = json.loads(result)
    names = {row["name"] for row in parsed}
    assert names == {"fn-one", "fn-two"}
    for row in parsed:
        assert row["runtime"] == "python3.12"
        assert "lastModified" in row


# ---------------------------------------------------------------------------
# lambda_describe_function
# ---------------------------------------------------------------------------

@mock_aws
def test_lambda_describe_function(aws_config):
    aws_config(lambda_tools)
    iam = boto3.client("iam", region_name="us-east-1")
    _create_role(iam)
    lam = boto3.client("lambda", region_name="us-east-1")
    _create_function(
        lam,
        name="my-function",
        MemorySize=256,
        Timeout=15,
        Environment={"Variables": {"FOO": "bar"}},
    )

    tools = get_all_tools(lambda_tools)
    result = tools["lambda_describe_function"](function_name="my-function", env="test")
    parsed = json.loads(result)
    assert parsed["name"] == "my-function"
    assert parsed["runtime"] == "python3.12"
    assert parsed["memorySize"] == 256
    assert parsed["timeout"] == 15
    assert parsed["handler"] == "lambda_function.handler"
    assert parsed["role"] == ROLE_ARN
    assert "lastModified" in parsed
    assert parsed["envVars"] == ["FOO"]
    assert "bar" not in json.dumps(parsed)


@mock_aws
def test_lambda_describe_function_not_found(aws_config):
    aws_config(lambda_tools)
    tools = get_all_tools(lambda_tools)
    result = tools["lambda_describe_function"](function_name="does-not-exist", env="test")
    assert "Error" in result
    assert "does-not-exist" in result or "ResourceNotFound" in result


# ---------------------------------------------------------------------------
# lambda_list_versions
# ---------------------------------------------------------------------------

@mock_aws
def test_lambda_list_versions(aws_config):
    aws_config(lambda_tools)
    iam = boto3.client("iam", region_name="us-east-1")
    _create_role(iam)
    lam = boto3.client("lambda", region_name="us-east-1")
    _create_function(lam, name="my-function")

    tools = get_all_tools(lambda_tools)
    result = tools["lambda_list_versions"](function_name="my-function", env="test")
    parsed = json.loads(result)
    versions = {row["version"] for row in parsed}
    assert "$LATEST" in versions


@mock_aws
def test_lambda_list_versions_not_found(aws_config):
    aws_config(lambda_tools)
    tools = get_all_tools(lambda_tools)
    # moto's list_versions_by_function for an unknown function returns an empty
    # list rather than raising, so this exercises the "no versions" branch.
    result = tools["lambda_list_versions"](function_name="does-not-exist", env="test")
    assert "No versions found" in result
    assert "does-not-exist" in result


# ---------------------------------------------------------------------------
# lambda_get_logs
# ---------------------------------------------------------------------------

@mock_aws
def test_lambda_get_logs_no_log_group(aws_config):
    aws_config(lambda_tools)
    tools = get_all_tools(lambda_tools)
    result = tools["lambda_get_logs"](function_name="ghost-function", env="test")
    assert "does not exist" in result
    assert "/aws/lambda/ghost-function" in result


@mock_aws
def test_lambda_get_logs_empty_log_group(aws_config):
    aws_config(lambda_tools)
    logs = boto3.client("logs", region_name="us-east-1")
    logs.create_log_group(logGroupName="/aws/lambda/quiet-function")

    tools = get_all_tools(lambda_tools)
    result = tools["lambda_get_logs"](function_name="quiet-function", env="test")
    assert "No logs found" in result
    assert "/aws/lambda/quiet-function" in result


@mock_aws
def test_lambda_get_logs_with_data(aws_config):
    aws_config(lambda_tools)
    logs = boto3.client("logs", region_name="us-east-1")
    log_group = "/aws/lambda/my-function"
    logs.create_log_group(logGroupName=log_group)
    logs.create_log_stream(logGroupName=log_group, logStreamName="stream-1")
    now = int(time.time() * 1000)
    logs.put_log_events(
        logGroupName=log_group,
        logStreamName="stream-1",
        logEvents=[
            {"timestamp": now, "message": "INFO starting up\n"},
            {"timestamp": now + 1000, "message": "INFO all good\n"},
        ],
    )

    tools = get_all_tools(lambda_tools)
    result = tools["lambda_get_logs"](function_name="my-function", env="test")
    parsed = json.loads(result)
    assert len(parsed) == 2
    messages = {row["message"] for row in parsed}
    assert "INFO starting up" in messages
    assert "INFO all good" in messages
    for row in parsed:
        assert row["logStream"] == "stream-1"


@mock_aws
def test_lambda_get_logs_limit_clamped(aws_config):
    aws_config(lambda_tools)
    logs = boto3.client("logs", region_name="us-east-1")
    log_group = "/aws/lambda/big-function"
    logs.create_log_group(logGroupName=log_group)
    logs.create_log_stream(logGroupName=log_group, logStreamName="stream-1")
    now = int(time.time() * 1000)
    events = [{"timestamp": now + i, "message": f"line {i}\n"} for i in range(5)]
    logs.put_log_events(logGroupName=log_group, logStreamName="stream-1", logEvents=events)

    tools = get_all_tools(lambda_tools)
    result = tools["lambda_get_logs"](function_name="big-function", limit=2, env="test")
    parsed = json.loads(result)
    # "most recent N": 5 events exist, so the LAST two are returned (the old code returned the
    # first two, i.e. the oldest, because CloudWatch has no descending order).
    assert len(parsed) == 2
    messages = [row["message"] for row in parsed]
    assert messages == ["line 3", "line 4"]


def test_lambda_get_logs_limit_clamped_to_200(aws_config):
    # limit=500 is clamped to 200: with 300 events available, exactly the 200 NEWEST come back.
    aws_config(lambda_tools)
    mock_logs_client = MagicMock()
    mock_logs_client.filter_log_events.return_value = {
        "events": [{"timestamp": 1_000 + i, "message": f"m{i}"} for i in range(300)]
    }

    with patch("providers.aws.lambda_tools.get_client", return_value=mock_logs_client):
        tools = get_all_tools(lambda_tools)
        result = tools["lambda_get_logs"](function_name="big-function", limit=500, env="test")

    rows = json.loads(result)
    assert len(rows) == 200
    assert rows[0]["message"] == "m100" and rows[-1]["message"] == "m299"
    call_kwargs = mock_logs_client.filter_log_events.call_args.kwargs
    assert "filterPattern" not in call_kwargs
    assert "startTime" in call_kwargs


# ---------------------------------------------------------------------------
# lambda_get_error_logs
# ---------------------------------------------------------------------------

@mock_aws
def test_lambda_get_error_logs_no_log_group(aws_config):
    aws_config(lambda_tools)
    tools = get_all_tools(lambda_tools)
    result = tools["lambda_get_error_logs"](function_name="ghost-function", env="test")
    assert "does not exist" in result


@mock_aws
def test_lambda_get_error_logs_empty(aws_config):
    aws_config(lambda_tools)
    logs = boto3.client("logs", region_name="us-east-1")
    log_group = "/aws/lambda/clean-function"
    logs.create_log_group(logGroupName=log_group)

    tools = get_all_tools(lambda_tools)
    result = tools["lambda_get_error_logs"](function_name="clean-function", env="test")
    assert "No error logs found" in result
    assert "/aws/lambda/clean-function" in result


def test_lambda_get_error_logs_with_data(aws_config):
    # moto's CloudWatch Logs mock does not actually honor `filterPattern` on
    # filter_log_events (it returns every stored event regardless of the
    # pattern), so real moto cannot prove the ERROR-only filtering behavior.
    # Mock at the client boundary instead, to assert lambda_get_error_logs
    # sends the expected filterPattern and correctly shapes whatever comes back.
    aws_config(lambda_tools)
    mock_logs_client = MagicMock()
    mock_logs_client.filter_log_events.return_value = {
        "events": [
            {"timestamp": 1000, "logStreamName": "stream-1", "message": "ERROR something broke\n"},
        ]
    }

    with patch("providers.aws.lambda_tools.get_client", return_value=mock_logs_client):
        tools = get_all_tools(lambda_tools)
        result = tools["lambda_get_error_logs"](function_name="error-function", env="test")

    call_kwargs = mock_logs_client.filter_log_events.call_args.kwargs
    assert call_kwargs["logGroupName"] == "/aws/lambda/error-function"
    assert "ERROR" in call_kwargs["filterPattern"]

    parsed = json.loads(result)
    assert len(parsed) == 1
    assert "ERROR something broke" in parsed[0]["message"]


def test_lambda_get_error_logs_limit_clamped_to_200(aws_config):
    aws_config(lambda_tools)
    mock_logs_client = MagicMock()
    mock_logs_client.filter_log_events.return_value = {
        "events": [{"timestamp": 1_000 + i, "message": f"ERROR m{i}"} for i in range(300)]
    }

    with patch("providers.aws.lambda_tools.get_client", return_value=mock_logs_client):
        tools = get_all_tools(lambda_tools)
        result = tools["lambda_get_error_logs"](function_name="many-errors-function", limit=500, env="test")

    assert len(json.loads(result)) == 200
    assert "filterPattern" in mock_logs_client.filter_log_events.call_args.kwargs


# ---------------------------------------------------------------------------
# Unknown environment path, one representative tool is enough per rules but we
# cover each tool since the failure mode (resolve_aws) is identical for all.
# ---------------------------------------------------------------------------

def test_lambda_list_functions_unknown_environment(aws_config):
    aws_config(lambda_tools)
    tools = get_all_tools(lambda_tools)
    result = tools["lambda_list_functions"](env="does-not-exist")
    assert "Unknown AWS environment" in result
    assert "does-not-exist" in result
    assert "Configured: test" in result


def test_lambda_describe_function_unknown_environment(aws_config):
    aws_config(lambda_tools)
    tools = get_all_tools(lambda_tools)
    result = tools["lambda_describe_function"](function_name="whatever", env="does-not-exist")
    assert "Unknown AWS environment" in result


def test_lambda_list_versions_unknown_environment(aws_config):
    aws_config(lambda_tools)
    tools = get_all_tools(lambda_tools)
    result = tools["lambda_list_versions"](function_name="whatever", env="does-not-exist")
    assert "Unknown AWS environment" in result


def test_lambda_get_logs_unknown_environment(aws_config):
    aws_config(lambda_tools)
    tools = get_all_tools(lambda_tools)
    result = tools["lambda_get_logs"](function_name="whatever", env="does-not-exist")
    assert "Unknown AWS environment" in result


def test_lambda_get_error_logs_unknown_environment(aws_config):
    aws_config(lambda_tools)
    tools = get_all_tools(lambda_tools)
    result = tools["lambda_get_error_logs"](function_name="whatever", env="does-not-exist")
    assert "Unknown AWS environment" in result
