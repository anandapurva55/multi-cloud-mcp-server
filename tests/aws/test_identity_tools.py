import boto3
from moto import mock_aws

from providers.aws import identity_tools
from tests.helpers import get_all_tools


@mock_aws
def test_aws_whoami_structure_and_values(aws_config):
    aws_config(identity_tools)

    # Independently call STS via the same moto interception to get the actual
    # expected account/arn values, rather than hardcoding moto's defaults.
    sts = boto3.client("sts", region_name="us-east-1")
    expected_identity = sts.get_caller_identity()

    tools = get_all_tools(identity_tools)
    result = tools["aws_whoami"](env="test")

    assert "Environment:" in result
    assert "Account:" in result
    assert "ARN:" in result
    assert "Region:" in result

    assert "test" in result
    assert "us-east-1" in result

    assert expected_identity["Account"] in result
    assert expected_identity["Arn"] in result


@mock_aws
def test_aws_whoami_uses_default_env_when_none_given(aws_config):
    aws_config(identity_tools)

    sts = boto3.client("sts", region_name="us-east-1")
    expected_identity = sts.get_caller_identity()

    tools = get_all_tools(identity_tools)
    result = tools["aws_whoami"]()

    assert "Environment:    test" in result or "test" in result
    assert expected_identity["Account"] in result


def test_aws_whoami_unknown_environment_returns_friendly_error(aws_config):
    aws_config(identity_tools)
    tools = get_all_tools(identity_tools)
    result = tools["aws_whoami"](env="does-not-exist")
    assert "Unknown AWS environment" in result
    assert "does-not-exist" in result
    assert "Configured: test" in result
