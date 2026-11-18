"""Pagination tests. Where possible these use moto's real paginated responses (not mocks), because
the original bug was reading only the first page: ECS list_services returns 10 per page by default,
S3 1000, Lambda 50, etc."""
import json
from unittest.mock import MagicMock

import boto3
from moto import mock_aws

from providers.aws import ecs_tools, s3_tools
from providers.aws.paging import MAX_LIST_ITEMS, paginate
from tests.helpers import get_all_tools


def test_paginate_asks_boto_for_limit_plus_one_so_truncation_is_detectable():
    client = MagicMock()
    paginator = MagicMock()
    paginator.paginate.return_value = iter([{"Items": [1, 2, 3]}])
    client.get_paginator.return_value = paginator
    items, truncated = paginate(client, "list_things", "Items", limit=2, Foo="bar")
    assert (items, truncated) == ([1, 2], True)
    client.get_paginator.assert_called_once_with("list_things")
    paginator.paginate.assert_called_once_with(PaginationConfig={"MaxItems": 3}, Foo="bar")


def test_paginate_exact_limit_is_not_truncated():
    client = MagicMock()
    client.get_paginator.return_value.paginate.return_value = iter([{"Items": [1, 2]}])
    assert paginate(client, "op", "Items", limit=2) == ([1, 2], False)


def test_paginate_joins_multiple_pages_and_tolerates_a_page_without_the_key():
    client = MagicMock()
    client.get_paginator.return_value.paginate.return_value = iter([{"Items": [1]}, {}, {"Items": [2, 3]}])
    assert paginate(client, "op", "Items", limit=10) == ([1, 2, 3], False)


def test_default_limit_is_200():
    assert MAX_LIST_ITEMS == 200


def test_ecs_list_services_follows_next_token_across_pages(aws_config):
    """A REAL boto3 client + botocore Stubber serving two pages (as AWS does: ECS pages at 10).
    The real paginator must follow nextToken; the old code read only the first page."""
    from unittest.mock import patch
    from botocore.stub import Stubber

    aws_config(ecs_tools)
    ecs = boto3.client("ecs", region_name="us-east-1", aws_access_key_id="x", aws_secret_access_key="x")
    arn = "arn:aws:ecs:us-east-1:123456789012:service/big/svc-{:02d}"
    with Stubber(ecs) as stub:
        stub.add_response("list_services", {"serviceArns": [arn.format(i) for i in range(10)], "nextToken": "page2"},
                          {"cluster": "big"})
        stub.add_response("list_services", {"serviceArns": [arn.format(i) for i in range(10, 12)]},
                          {"cluster": "big", "nextToken": "page2"})
        with patch("providers.aws.ecs_tools.get_client", return_value=ecs):
            result = get_all_tools(ecs_tools)["ecs_list_services"](cluster="big", env="test")
        stub.assert_no_pending_responses()   # both pages were requested
    names = {line.strip("  • ") for line in result.splitlines()}
    assert names == {f"svc-{i:02d}" for i in range(12)}
    assert "more exist" not in result   # complete, so no truncation note


@mock_aws
def test_ecs_list_services_works_against_moto_too(aws_config):
    aws_config(ecs_tools)
    ecs = boto3.client("ecs", region_name="us-east-1")
    ecs.create_cluster(clusterName="big")
    ecs.register_task_definition(family="td", containerDefinitions=[{"name": "c", "image": "nginx", "memory": 64}])
    for i in range(12):
        ecs.create_service(cluster="big", serviceName=f"svc-{i:02d}", taskDefinition="td", desiredCount=0)
    result = get_all_tools(ecs_tools)["ecs_list_services"](cluster="big", env="test")
    assert {line.strip("  • ") for line in result.splitlines()} == {f"svc-{i:02d}" for i in range(12)}


@mock_aws
def test_s3_list_objects_reports_truncation_with_real_paginator(aws_config):
    aws_config(s3_tools)
    s3 = boto3.client("s3", region_name="us-east-1")
    s3.create_bucket(Bucket="bucket-b")
    for i in range(7):
        s3.put_object(Bucket="bucket-b", Key=f"k{i}", Body=b"x")
    tools = get_all_tools(s3_tools)

    result = tools["s3_list_objects"](bucket="bucket-b", limit=3, env="test")
    body, note = result.split("\n[", 1)
    assert [r["key"] for r in json.loads(body)] == ["k0", "k1", "k2"]
    assert "more exist" in note

    exact = tools["s3_list_objects"](bucket="bucket-b", limit=7, env="test")
    assert len(json.loads(exact)) == 7   # exactly the limit: pure JSON, no note


@mock_aws
def test_s3_list_buckets_uses_paginator_and_lists_all(aws_config):
    aws_config(s3_tools)
    s3 = boto3.client("s3", region_name="us-east-1")
    for i in range(3):
        s3.create_bucket(Bucket=f"bucket-{i}")
    result = get_all_tools(s3_tools)["s3_list_buckets"](env="test")
    assert all(f"bucket-{i}" in result for i in range(3))
