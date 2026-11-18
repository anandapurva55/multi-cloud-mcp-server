import json

import boto3
from moto import mock_aws

from providers.aws import s3_tools
from tests.helpers import get_all_tools


@mock_aws
def test_s3_list_buckets_empty(aws_config):
    aws_config(s3_tools)
    tools = get_all_tools(s3_tools)
    result = tools["s3_list_buckets"](env="test")
    assert result == "No S3 buckets found."


@mock_aws
def test_s3_list_buckets_with_data(aws_config):
    aws_config(s3_tools)
    s3 = boto3.client("s3", region_name="us-east-1")
    s3.create_bucket(Bucket="my-bucket")
    s3.create_bucket(Bucket="another-bucket")

    tools = get_all_tools(s3_tools)
    result = tools["s3_list_buckets"](env="test")
    assert "my-bucket" in result
    assert "another-bucket" in result


@mock_aws
def test_s3_list_objects_empty(aws_config):
    aws_config(s3_tools)
    s3 = boto3.client("s3", region_name="us-east-1")
    s3.create_bucket(Bucket="empty-bucket")

    tools = get_all_tools(s3_tools)
    result = tools["s3_list_objects"](bucket="empty-bucket", env="test")
    assert "No objects found" in result


@mock_aws
def test_s3_list_objects_with_data(aws_config):
    aws_config(s3_tools)
    s3 = boto3.client("s3", region_name="us-east-1")
    s3.create_bucket(Bucket="data-bucket")
    s3.put_object(Bucket="data-bucket", Key="logs/2026/01/file.txt", Body=b"hello")
    s3.put_object(Bucket="data-bucket", Key="other/file.txt", Body=b"world")

    tools = get_all_tools(s3_tools)
    result = tools["s3_list_objects"](bucket="data-bucket", prefix="logs/", env="test")
    parsed = json.loads(result)
    assert len(parsed) == 1
    assert parsed[0]["key"] == "logs/2026/01/file.txt"
    assert parsed[0]["size"] == 5


@mock_aws
def test_s3_list_objects_limit_clamped(aws_config):
    aws_config(s3_tools)
    s3 = boto3.client("s3", region_name="us-east-1")
    s3.create_bucket(Bucket="big-bucket")
    for i in range(5):
        s3.put_object(Bucket="big-bucket", Key=f"file{i}.txt", Body=b"x")

    tools = get_all_tools(s3_tools)
    result = tools["s3_list_objects"](bucket="big-bucket", limit=2, env="test")
    body, note = result.split("\n[", 1)
    assert len(json.loads(body)) == 2
    assert "more exist" in note  # 5 objects exist, only 2 shown: say so


def test_unknown_environment_returns_friendly_error(aws_config):
    aws_config(s3_tools)
    tools = get_all_tools(s3_tools)
    result = tools["s3_list_buckets"](env="does-not-exist")
    assert "Unknown AWS environment" in result
    assert "does-not-exist" in result
    assert "Configured: test" in result
