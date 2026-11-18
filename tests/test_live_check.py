"""
Tests for live_check.py's check_gcp/check_aws logic.

These mock the underlying credential/API calls, since live_check.py is
specifically meant to run against real clouds; unit tests here only verify
its own pass/fail classification logic is correct, not real connectivity.
"""
from unittest.mock import MagicMock, patch

from config import GCPEnvironment, AWSEnvironment
from live_check import check_gcp, check_aws


def _gcp_env():
    return GCPEnvironment(name="test", project_id="test-project", region="us-central1", auth_mode="adc")


def _aws_env():
    return AWSEnvironment(name="test", region="us-east-1", auth_mode="profile", profile="default")


def test_check_gcp_success():
    with patch("live_check.gcp_get_credentials", return_value=MagicMock()), \
         patch("google.cloud.bigquery.Client") as MockClient:
        MockClient.return_value.list_datasets.return_value = []
        ok, detail = check_gcp(_gcp_env())
    assert ok is True
    assert "BigQuery reachable" in detail


def test_check_gcp_credential_failure():
    with patch("live_check.gcp_get_credentials", side_effect=RuntimeError("no ADC found")):
        ok, detail = check_gcp(_gcp_env())
    assert ok is False
    assert "credential resolution failed" in detail
    assert "no ADC found" in detail


def test_check_gcp_credentials_ok_but_api_disabled_is_still_ok():
    with patch("live_check.gcp_get_credentials", return_value=MagicMock()), \
         patch("google.cloud.bigquery.Client") as MockClient:
        MockClient.return_value.list_datasets.side_effect = RuntimeError("SERVICE_DISABLED")
        ok, detail = check_gcp(_gcp_env())
    # Credentials being valid is the important signal; a disabled API on this
    # particular project is not a credential problem, so this must still be OK.
    assert ok is True
    assert "credentials OK" in detail
    assert "SERVICE_DISABLED" in detail


def test_check_aws_success():
    fake_identity = {"account": "123456789012", "arn": "arn:aws:iam::123456789012:user/test", "userId": "AID..."}
    with patch("live_check.aws_verify_credentials", return_value=fake_identity):
        ok, detail = check_aws(_aws_env())
    assert ok is True
    assert "123456789012" in detail
    assert "arn:aws:iam::123456789012:user/test" in detail


def test_check_aws_failure():
    with patch("live_check.aws_verify_credentials", side_effect=RuntimeError("InvalidClientTokenId")):
        ok, detail = check_aws(_aws_env())
    assert ok is False
    assert "credential verification failed" in detail
    assert "InvalidClientTokenId" in detail
