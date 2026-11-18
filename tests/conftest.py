"""
Shared pytest fixtures for testing tool modules in isolation, without a real .env,
real GCP credentials, or real AWS credentials.

Each tool module does `from config import CONFIG` at import time, which binds a
name in that module's own namespace. Patching config.CONFIG after the fact does
NOT affect already-imported tool modules, so these fixtures patch the CONFIG
attribute on the tool module itself (via monkeypatch.setattr(module, "CONFIG", ...)).

For AWS: use auth_mode="keys" with fake static credentials so boto3.Session()
succeeds without touching ~/.aws, then wrap the test body in moto's @mock_aws
decorator so the real auth.py code path runs against moto's in-memory AWS.

For GCP: there is no moto-equivalent emulator, so GCP tests must mock at the
client-class boundary (bigquery.Client, run_v2.ServicesClient, run_v2.RevisionsClient,
gcp_logging.Client, requests.get) and mock providers.gcp.auth.get_credentials /
get_access_token so no real ADC call is attempted.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest

from config import AppConfig, AWSEnvironment, GCPEnvironment


@pytest.fixture(autouse=True)
def _never_touch_real_env_file(monkeypatch, tmp_path_factory):
    """config.default_env_path() is anchored to the repo, so without this a test that
    forgot to pass an explicit path could read or overwrite the developer's real .env."""
    monkeypatch.setenv("MULTICLOUD_MCP_ENV_FILE",
                       str(tmp_path_factory.mktemp("isolated_env") / ".env"))


@pytest.fixture
def gcp_config(monkeypatch):
    """Returns patch(module) -> GCPEnvironment. Call it with the tool module under test."""
    cfg = AppConfig()
    cfg.gcp_environments["test"] = GCPEnvironment(
        name="test", project_id="test-project", region="us-central1", auth_mode="adc",
    )
    cfg.gcp_default = "test"

    def patch(module):
        monkeypatch.setattr(module, "CONFIG", cfg)
        return cfg.gcp_environments["test"]

    return patch


@pytest.fixture
def aws_config(monkeypatch):
    """Returns patch(module) -> AWSEnvironment. Call it with the tool module under test."""
    cfg = AppConfig()
    cfg.aws_environments["test"] = AWSEnvironment(
        name="test", region="us-east-1", auth_mode="keys",
        access_key_id="testing", secret_access_key="testing", session_token="testing",
    )
    cfg.aws_default = "test"

    def patch(module):
        monkeypatch.setattr(module, "CONFIG", cfg)
        return cfg.aws_environments["test"]

    return patch
