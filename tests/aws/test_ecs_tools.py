import json
import time
from unittest.mock import MagicMock, patch

import boto3
from moto import mock_aws

from providers.aws import ecs_tools
from tests.helpers import get_all_tools, mock_paginators


def _register_task_def_with_awslogs(ecs, family="test-td", log_group="/ecs/test-service"):
    return ecs.register_task_definition(
        family=family,
        containerDefinitions=[{
            "name": "app",
            "image": "nginx",
            "memory": 128,
            "logConfiguration": {
                "logDriver": "awslogs",
                "options": {
                    "awslogs-group": log_group,
                    "awslogs-region": "us-east-1",
                    "awslogs-stream-prefix": "ecs",
                },
            },
        }],
    )


def _put_log_events(logs, log_group, messages, stream_name="stream1"):
    logs.create_log_group(logGroupName=log_group)
    logs.create_log_stream(logGroupName=log_group, logStreamName=stream_name)
    base_ts = int(time.time() * 1000)
    events = [{"timestamp": base_ts + i, "message": m} for i, m in enumerate(messages)]
    logs.put_log_events(logGroupName=log_group, logStreamName=stream_name, logEvents=events)


# ---------------------------------------------------------------------------
# ecs_list_clusters
# ---------------------------------------------------------------------------

@mock_aws
def test_ecs_list_clusters_empty(aws_config):
    aws_config(ecs_tools)
    tools = get_all_tools(ecs_tools)
    result = tools["ecs_list_clusters"](env="test")
    assert result == "No ECS clusters found."


@mock_aws
def test_ecs_list_clusters_with_data(aws_config):
    aws_config(ecs_tools)
    ecs = boto3.client("ecs", region_name="us-east-1")
    ecs.create_cluster(clusterName="cluster-one")
    ecs.create_cluster(clusterName="cluster-two")

    tools = get_all_tools(ecs_tools)
    result = tools["ecs_list_clusters"](env="test")
    assert "cluster-one" in result
    assert "cluster-two" in result


# ---------------------------------------------------------------------------
# ecs_list_services
# ---------------------------------------------------------------------------

@mock_aws
def test_ecs_list_services_empty(aws_config):
    aws_config(ecs_tools)
    ecs = boto3.client("ecs", region_name="us-east-1")
    ecs.create_cluster(clusterName="test-cluster")

    tools = get_all_tools(ecs_tools)
    result = tools["ecs_list_services"](cluster="test-cluster", env="test")
    assert result == "No services found in cluster 'test-cluster'."


@mock_aws
def test_ecs_list_services_with_data(aws_config):
    aws_config(ecs_tools)
    ecs = boto3.client("ecs", region_name="us-east-1")
    ecs.create_cluster(clusterName="test-cluster")
    ecs.register_task_definition(family="test-td", containerDefinitions=[{"name": "app", "image": "nginx", "memory": 128}])
    ecs.create_service(cluster="test-cluster", serviceName="service-a", taskDefinition="test-td", desiredCount=1)
    ecs.create_service(cluster="test-cluster", serviceName="service-b", taskDefinition="test-td", desiredCount=1)

    tools = get_all_tools(ecs_tools)
    result = tools["ecs_list_services"](cluster="test-cluster", env="test")
    assert "service-a" in result
    assert "service-b" in result


# ---------------------------------------------------------------------------
# ecs_describe_service
# ---------------------------------------------------------------------------

@mock_aws
def test_ecs_describe_service_not_found(aws_config):
    aws_config(ecs_tools)
    ecs = boto3.client("ecs", region_name="us-east-1")
    ecs.create_cluster(clusterName="test-cluster")

    tools = get_all_tools(ecs_tools)
    result = tools["ecs_describe_service"](cluster="test-cluster", service_name="ghost-service", env="test")
    # describe_services reports unknown services in `failures` (not an exception); that used to
    # surface as "list index out of range" in the log tools.
    assert "Service 'ghost-service' not found in cluster 'test-cluster'" in result
    assert "MISSING" in result


@mock_aws
def test_ecs_describe_service_with_data(aws_config):
    aws_config(ecs_tools)
    ecs = boto3.client("ecs", region_name="us-east-1")
    ecs.create_cluster(clusterName="test-cluster")
    ecs.register_task_definition(family="test-td", containerDefinitions=[{"name": "app", "image": "nginx", "memory": 128}])
    ecs.create_service(cluster="test-cluster", serviceName="test-service", taskDefinition="test-td", desiredCount=2)

    tools = get_all_tools(ecs_tools)
    result = tools["ecs_describe_service"](cluster="test-cluster", service_name="test-service", env="test")
    parsed = json.loads(result)
    assert parsed["name"] == "test-service"
    assert parsed["status"] == "ACTIVE"
    assert parsed["desiredCount"] == 2
    assert "test-td" in parsed["taskDefinition"]
    assert len(parsed["deployments"]) == 1
    assert parsed["deployments"][0]["status"] == "PRIMARY"
    assert parsed["deployments"][0]["desiredCount"] == 2


# ---------------------------------------------------------------------------
# ecs_list_tasks
# ---------------------------------------------------------------------------

@mock_aws
def test_ecs_list_tasks_no_tasks(aws_config):
    aws_config(ecs_tools)
    ecs = boto3.client("ecs", region_name="us-east-1")
    ecs.create_cluster(clusterName="test-cluster")
    ecs.register_task_definition(family="test-td", containerDefinitions=[{"name": "app", "image": "nginx", "memory": 128}])
    ecs.create_service(cluster="test-cluster", serviceName="test-service", taskDefinition="test-td", desiredCount=1)

    tools = get_all_tools(ecs_tools)
    result = tools["ecs_list_tasks"](cluster="test-cluster", service_name="test-service", env="test")
    assert result == "No tasks found for service 'test-service'."


@mock_aws
def test_ecs_list_tasks_with_data(aws_config):
    aws_config(ecs_tools)
    ecs = boto3.client("ecs", region_name="us-east-1")
    ec2 = boto3.client("ec2", region_name="us-east-1")
    ecs.create_cluster(clusterName="test-cluster")

    # moto needs a registered container instance before run_task will place anything.
    instance = ec2.run_instances(ImageId="ami-12345678", MinCount=1, MaxCount=1, InstanceType="t2.micro")["Instances"][0]
    doc = json.dumps({"instanceId": instance["InstanceId"], "region": "us-east-1"})
    ecs.register_container_instance(cluster="test-cluster", instanceIdentityDocument=doc)

    ecs.register_task_definition(family="test-td", containerDefinitions=[{"name": "app", "image": "nginx", "memory": 128}])
    ecs.create_service(cluster="test-cluster", serviceName="test-service", taskDefinition="test-td", desiredCount=1)
    ecs.run_task(cluster="test-cluster", taskDefinition="test-td")

    tools = get_all_tools(ecs_tools)
    result = tools["ecs_list_tasks"](cluster="test-cluster", service_name="test-service", env="test")
    parsed = json.loads(result)
    assert len(parsed) == 1
    assert "taskDefinitionArn" in parsed[0]
    assert "test-td" in parsed[0]["taskDefinitionArn"]
    assert "lastStatus" in parsed[0]
    # taskArn field is reduced to the trailing id segment, not the full ARN.
    assert "arn:aws:ecs" not in parsed[0]["taskArn"]


@mock_aws
def test_ecs_list_tasks_limit_clamped(aws_config):
    aws_config(ecs_tools)
    fake_arns = [f"arn:aws:ecs:us-east-1:123456789012:task/test-cluster/task-{i}" for i in range(60)]

    mock_ecs = MagicMock()
    mock_paginators(mock_ecs, list_tasks=[{"taskArns": fake_arns}])

    def describe_tasks(cluster, tasks):
        return {"tasks": [
            {"taskArn": arn, "lastStatus": "RUNNING", "healthStatus": "HEALTHY",
             "startedAt": None, "taskDefinitionArn": "arn:aws:ecs:us-east-1:123456789012:task-definition/test-td:1"}
            for arn in tasks
        ]}

    mock_ecs.describe_tasks.side_effect = describe_tasks

    with patch("providers.aws.ecs_tools.get_client", return_value=mock_ecs):
        tools = get_all_tools(ecs_tools)
        result = tools["ecs_list_tasks"](cluster="test-cluster", service_name="test-service", limit=1000, env="test")

    body, note = result.split("\n[", 1)
    parsed = json.loads(body)
    assert len(parsed) == 50
    assert "more exist" in note
    called_tasks = mock_ecs.describe_tasks.call_args.kwargs["tasks"]
    assert len(called_tasks) == 50


# ---------------------------------------------------------------------------
# ecs_get_logs / ecs_get_error_logs
# ---------------------------------------------------------------------------

@mock_aws
def test_ecs_get_logs_no_awslogs_driver(aws_config):
    aws_config(ecs_tools)
    ecs = boto3.client("ecs", region_name="us-east-1")
    ecs.create_cluster(clusterName="test-cluster")
    ecs.register_task_definition(family="test-td", containerDefinitions=[{"name": "app", "image": "nginx", "memory": 128}])
    ecs.create_service(cluster="test-cluster", serviceName="test-service", taskDefinition="test-td", desiredCount=1)

    tools = get_all_tools(ecs_tools)
    result = tools["ecs_get_logs"](cluster="test-cluster", service_name="test-service", env="test")
    assert result == "Error: Service 'test-service' has no awslogs log driver configured."


@mock_aws
def test_ecs_get_logs_log_group_not_found(aws_config):
    aws_config(ecs_tools)
    ecs = boto3.client("ecs", region_name="us-east-1")
    ecs.create_cluster(clusterName="test-cluster")
    _register_task_def_with_awslogs(ecs, log_group="/ecs/never-created")
    ecs.create_service(cluster="test-cluster", serviceName="test-service", taskDefinition="test-td", desiredCount=1)

    tools = get_all_tools(ecs_tools)
    result = tools["ecs_get_logs"](cluster="test-cluster", service_name="test-service", env="test")
    assert result == "Log group '/ecs/never-created' does not exist (yet)."


@mock_aws
def test_ecs_get_logs_empty_log_group(aws_config):
    aws_config(ecs_tools)
    ecs = boto3.client("ecs", region_name="us-east-1")
    logs = boto3.client("logs", region_name="us-east-1")
    _register_task_def_with_awslogs(ecs, log_group="/ecs/test-service")
    ecs.create_cluster(clusterName="test-cluster")
    ecs.create_service(cluster="test-cluster", serviceName="test-service", taskDefinition="test-td", desiredCount=1)
    logs.create_log_group(logGroupName="/ecs/test-service")

    tools = get_all_tools(ecs_tools)
    result = tools["ecs_get_logs"](cluster="test-cluster", service_name="test-service", env="test")
    assert result == "No logs found in '/ecs/test-service' in the last 60 minutes."


@mock_aws
def test_ecs_get_logs_with_data(aws_config):
    aws_config(ecs_tools)
    ecs = boto3.client("ecs", region_name="us-east-1")
    logs = boto3.client("logs", region_name="us-east-1")
    ecs.create_cluster(clusterName="test-cluster")
    _register_task_def_with_awslogs(ecs, log_group="/ecs/test-service")
    ecs.create_service(cluster="test-cluster", serviceName="test-service", taskDefinition="test-td", desiredCount=1)
    _put_log_events(logs, "/ecs/test-service", ["INFO starting up", "INFO all good"])

    tools = get_all_tools(ecs_tools)
    result = tools["ecs_get_logs"](cluster="test-cluster", service_name="test-service", env="test")
    parsed = json.loads(result)
    messages = [row["message"] for row in parsed]
    assert "INFO starting up" in messages
    assert "INFO all good" in messages
    assert all("logStream" in row and "timestamp" in row for row in parsed)


@mock_aws
def test_ecs_get_error_logs_with_data(aws_config):
    aws_config(ecs_tools)
    ecs = boto3.client("ecs", region_name="us-east-1")
    logs = boto3.client("logs", region_name="us-east-1")
    ecs.create_cluster(clusterName="test-cluster")
    _register_task_def_with_awslogs(ecs, log_group="/ecs/test-service")
    ecs.create_service(cluster="test-cluster", serviceName="test-service", taskDefinition="test-td", desiredCount=1)
    _put_log_events(logs, "/ecs/test-service", ["INFO starting up", "ERROR something broke", "INFO all good"])

    tools = get_all_tools(ecs_tools)
    result = tools["ecs_get_error_logs"](cluster="test-cluster", service_name="test-service", env="test")
    parsed = json.loads(result)
    messages = [row["message"] for row in parsed]
    # moto's filter_log_events does not actually apply filterPattern server-side, so we can only
    # assert the error line is present (a superset of the "real AWS" behavior), not that it's the
    # only line returned. The filterPattern construction itself is verified separately below.
    assert "ERROR something broke" in messages


@mock_aws
def test_ecs_get_error_logs_no_errors_found(aws_config):
    aws_config(ecs_tools)
    ecs = boto3.client("ecs", region_name="us-east-1")
    logs = boto3.client("logs", region_name="us-east-1")
    ecs.create_cluster(clusterName="test-cluster")
    _register_task_def_with_awslogs(ecs, log_group="/ecs/test-service")
    ecs.create_service(cluster="test-cluster", serviceName="test-service", taskDefinition="test-td", desiredCount=1)
    logs.create_log_group(logGroupName="/ecs/test-service")

    tools = get_all_tools(ecs_tools)
    result = tools["ecs_get_error_logs"](cluster="test-cluster", service_name="test-service", env="test")
    assert result == "No error logs found in '/ecs/test-service' in the last 60 minutes."


def test_ecs_get_error_logs_sends_error_filter_pattern_and_clamps_limit(aws_config):
    aws_config(ecs_tools)
    mock_logs = MagicMock()
    mock_logs.filter_log_events.return_value = {"events": []}

    mock_ecs = MagicMock()
    mock_ecs.describe_services.return_value = {"services": [{"taskDefinition": "test-td-arn"}]}
    mock_ecs.describe_task_definition.return_value = {"taskDefinition": {"containerDefinitions": [
        {"name": "app", "logConfiguration": {"logDriver": "awslogs", "options": {"awslogs-group": "/ecs/test-service"}}}
    ]}}

    def fake_get_client(env, service_name):
        if service_name == "logs":
            return mock_logs
        return mock_ecs

    with patch("providers.aws.ecs_tools.get_client", side_effect=fake_get_client):
        tools = get_all_tools(ecs_tools)
        tools["ecs_get_error_logs"](cluster="test-cluster", service_name="test-service", limit=9999, env="test")

    kwargs = mock_logs.filter_log_events.call_args.kwargs
    assert kwargs["filterPattern"] == "?ERROR ?Error ?CRITICAL ?FATAL ?Exception ?Traceback"
    assert kwargs["logGroupName"] == "/ecs/test-service"
    # a bounded search window is always sent; `limit` only caps one page so it is no longer passed
    assert "startTime" in kwargs and "limit" not in kwargs


def test_ecs_get_logs_has_no_filter_pattern_and_clamps_limit(aws_config):
    aws_config(ecs_tools)
    mock_logs = MagicMock()
    mock_logs.filter_log_events.return_value = {"events": []}

    mock_ecs = MagicMock()
    mock_ecs.describe_services.return_value = {"services": [{"taskDefinition": "test-td-arn"}]}
    mock_ecs.describe_task_definition.return_value = {"taskDefinition": {"containerDefinitions": [
        {"name": "app", "logConfiguration": {"logDriver": "awslogs", "options": {"awslogs-group": "/ecs/test-service"}}}
    ]}}

    def fake_get_client(env, service_name):
        if service_name == "logs":
            return mock_logs
        return mock_ecs

    with patch("providers.aws.ecs_tools.get_client", side_effect=fake_get_client):
        tools = get_all_tools(ecs_tools)
        tools["ecs_get_logs"](cluster="test-cluster", service_name="test-service", limit=9999, env="test")

    kwargs = mock_logs.filter_log_events.call_args.kwargs
    assert "filterPattern" not in kwargs
    assert "startTime" in kwargs and "limit" not in kwargs


# ---------------------------------------------------------------------------
# Unknown environment path (friendly_errors + CONFIG.resolve_aws), one per tool
# ---------------------------------------------------------------------------

def test_ecs_list_clusters_unknown_environment(aws_config):
    aws_config(ecs_tools)
    tools = get_all_tools(ecs_tools)
    result = tools["ecs_list_clusters"](env="does-not-exist")
    assert "Unknown AWS environment" in result
    assert "does-not-exist" in result


def test_ecs_list_services_unknown_environment(aws_config):
    aws_config(ecs_tools)
    tools = get_all_tools(ecs_tools)
    result = tools["ecs_list_services"](cluster="test-cluster", env="does-not-exist")
    assert "Unknown AWS environment" in result


def test_ecs_describe_service_unknown_environment(aws_config):
    aws_config(ecs_tools)
    tools = get_all_tools(ecs_tools)
    result = tools["ecs_describe_service"](cluster="test-cluster", service_name="test-service", env="does-not-exist")
    assert "Unknown AWS environment" in result


def test_ecs_list_tasks_unknown_environment(aws_config):
    aws_config(ecs_tools)
    tools = get_all_tools(ecs_tools)
    result = tools["ecs_list_tasks"](cluster="test-cluster", service_name="test-service", env="does-not-exist")
    assert "Unknown AWS environment" in result


def test_ecs_get_logs_unknown_environment(aws_config):
    aws_config(ecs_tools)
    tools = get_all_tools(ecs_tools)
    result = tools["ecs_get_logs"](cluster="test-cluster", service_name="test-service", env="does-not-exist")
    assert "Unknown AWS environment" in result


def test_ecs_get_error_logs_unknown_environment(aws_config):
    aws_config(ecs_tools)
    tools = get_all_tools(ecs_tools)
    result = tools["ecs_get_error_logs"](cluster="test-cluster", service_name="test-service", env="does-not-exist")
    assert "Unknown AWS environment" in result
