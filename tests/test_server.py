"""
Tests for server.py's conditional tool registration and fail-fast behavior.

server.py has import-time side effects (it registers tools and calls sys.exit(1) if no
provider is configured), so it cannot be safely imported repeatedly in-process with
different CONFIG states. Every test here runs it as a real subprocess instead, each with
its own isolated temp directory as the working directory (so a per-test .env file never
touches the shared repo root, where other test files' processes might be running
concurrently) and PYTHONPATH pointed at the repo so the real source modules resolve.
"""
import os
import subprocess
import sys
import tempfile

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PYTHON = os.path.join(REPO, ".venv", "bin", "python3")
if not os.path.isfile(PYTHON):
    PYTHON = sys.executable


def _run(code: str, env_file_contents: str = None, timeout: float = 15):
    with tempfile.TemporaryDirectory() as tmp_dir:
        if env_file_contents is not None:
            with open(os.path.join(tmp_dir, ".env"), "w") as f:
                f.write(env_file_contents)
        env = dict(os.environ)
        env["PYTHONPATH"] = REPO
        env["MULTICLOUD_MCP_ENV_FILE"] = os.path.join(tmp_dir, ".env")
        for key in list(env):
            if key.startswith(("GCP_", "AWS_")):
                del env[key]
        return subprocess.run(
            [PYTHON, "-c", code],
            cwd=tmp_dir,
            env=env,
            capture_output=True,
            text=True,
            timeout=timeout,
        )


def test_fails_fast_with_no_env_configured():
    result = _run("import server", env_file_contents="")
    assert result.returncode == 1
    assert "No cloud providers configured" in result.stderr
    assert "setup_wizard.py" in result.stderr


def test_gcp_only_registers_gcp_tools_only():
    env_contents = (
        "GCP_ENVIRONMENTS=dev\n"
        "GCP_DEFAULT_ENVIRONMENT=dev\n"
        "GCP_ENV_DEV_PROJECT_ID=test-project\n"
        "GCP_ENV_DEV_REGION=us-central1\n"
        "GCP_ENV_DEV_AUTH_MODE=adc\n"
        "AWS_ENVIRONMENTS=\n"
        "AWS_DEFAULT_ENVIRONMENT=\n"
    )
    code = (
        "import server\n"
        "names = sorted(server.mcp._tool_manager._tools.keys())\n"
        "print(','.join(names))\n"
    )
    result = _run(code, env_file_contents=env_contents)
    assert result.returncode == 0, result.stderr
    names = result.stdout.strip().split(",")

    expected_gcp = {
        "bq_list_datasets", "bq_list_tables", "bq_describe_table", "bq_query", "bq_preview_table",
        "cloudbuild_list_builds", "cloudbuild_get_logs", "cloudbuild_describe_build",
        "cloudrun_list_services", "cloudrun_describe_service", "cloudrun_list_revisions",
        "cloudrun_get_logs", "cloudrun_get_error_logs",
        "list_environments",
    }
    assert set(names) == expected_gcp

    forbidden_aws_prefixes = ("athena_", "codebuild_", "ecs_", "lambda_", "s3_", "aws_whoami")
    assert not any(n.startswith(forbidden_aws_prefixes) or n == "aws_whoami" for n in names)


def test_aws_only_registers_aws_tools_only():
    env_contents = (
        "GCP_ENVIRONMENTS=\n"
        "GCP_DEFAULT_ENVIRONMENT=\n"
        "AWS_ENVIRONMENTS=staging\n"
        "AWS_DEFAULT_ENVIRONMENT=staging\n"
        "AWS_ENV_STAGING_REGION=us-east-1\n"
        "AWS_ENV_STAGING_AUTH_MODE=profile\n"
        "AWS_ENV_STAGING_PROFILE=default\n"
    )
    code = (
        "import server\n"
        "names = sorted(server.mcp._tool_manager._tools.keys())\n"
        "print(','.join(names))\n"
    )
    result = _run(code, env_file_contents=env_contents)
    assert result.returncode == 0, result.stderr
    names = result.stdout.strip().split(",")

    expected_aws = {
        "athena_list_databases", "athena_list_tables", "athena_describe_table", "athena_query", "athena_get_query_results",
        "athena_preview_table",
        "codebuild_list_projects", "codebuild_list_builds", "codebuild_get_logs", "codebuild_describe_build",
        "ecs_list_clusters", "ecs_list_services", "ecs_describe_service", "ecs_list_tasks",
        "ecs_get_logs", "ecs_get_error_logs",
        "lambda_list_functions", "lambda_describe_function", "lambda_list_versions",
        "lambda_get_logs", "lambda_get_error_logs",
        "s3_list_buckets", "s3_list_objects",
        "aws_whoami",
        "list_environments",
    }
    assert set(names) == expected_aws

    forbidden_gcp_prefixes = ("bq_", "cloudbuild_", "cloudrun_")
    assert not any(n.startswith(forbidden_gcp_prefixes) for n in names)


def test_mixed_provider_registers_both_and_totals_38_tools():
    env_contents = (
        "GCP_ENVIRONMENTS=dev\n"
        "GCP_DEFAULT_ENVIRONMENT=dev\n"
        "GCP_ENV_DEV_PROJECT_ID=test-project\n"
        "GCP_ENV_DEV_REGION=us-central1\n"
        "GCP_ENV_DEV_AUTH_MODE=adc\n"
        "AWS_ENVIRONMENTS=staging\n"
        "AWS_DEFAULT_ENVIRONMENT=staging\n"
        "AWS_ENV_STAGING_REGION=us-east-1\n"
        "AWS_ENV_STAGING_AUTH_MODE=profile\n"
        "AWS_ENV_STAGING_PROFILE=default\n"
    )
    code = (
        "import server\n"
        "print(len(server.mcp._tool_manager._tools))\n"
    )
    result = _run(code, env_file_contents=env_contents)
    assert result.returncode == 0, result.stderr
    count = int(result.stdout.strip())
    assert count == 38


def test_list_environments_tool_reports_configured_names():
    env_contents = (
        "GCP_ENVIRONMENTS=dev,prod\n"
        "GCP_DEFAULT_ENVIRONMENT=dev\n"
        "GCP_ENV_DEV_PROJECT_ID=test-project-dev\n"
        "GCP_ENV_DEV_REGION=us-central1\n"
        "GCP_ENV_DEV_AUTH_MODE=adc\n"
        "GCP_ENV_PROD_PROJECT_ID=test-project-prod\n"
        "GCP_ENV_PROD_REGION=us-central1\n"
        "GCP_ENV_PROD_AUTH_MODE=adc\n"
        "AWS_ENVIRONMENTS=\n"
        "AWS_DEFAULT_ENVIRONMENT=\n"
    )
    code = (
        "import server\n"
        "print(server.list_environments())\n"
    )
    result = _run(code, env_file_contents=env_contents)
    assert result.returncode == 0, result.stderr
    output = result.stdout
    assert "dev (default)" in output
    assert "prod" in output
    assert "test-project-dev" in output
    assert "AWS: not configured." in output


_BOTH = (
    "GCP_ENVIRONMENTS=dev\nGCP_ENV_DEV_PROJECT_ID=p\n"
    "AWS_ENVIRONMENTS=stg\nAWS_ENV_STG_REGION=us-east-1\n"
)


def test_every_registered_tool_is_declared_read_only():
    code = (
        "import server\n"
        "bad = [n for n, t in server.mcp._tool_manager._tools.items()\n"
        "       if not (t.annotations and t.annotations.readOnlyHint is True and t.annotations.destructiveHint is False)]\n"
        "print('BAD:' + ','.join(bad))\n"
    )
    result = _run(code, env_file_contents=_BOTH)
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "BAD:"


def test_invalid_configuration_fails_fast_with_a_clear_message():
    result = _run("import server", env_file_contents="GCP_ENVIRONMENTS=dev\nGCP_ENV_DEV_PROJECT_ID=p\nGCP_ENV_DEV_AUTH_MODE=keyfile\n")
    assert result.returncode == 1
    assert "Invalid configuration" in result.stderr
    assert "GCP_ENV_DEV_AUTH_MODE='keyfile'" in result.stderr


def test_env_file_is_found_when_launched_from_an_unrelated_directory():
    """MCP clients launch servers from arbitrary working directories. The config location must
    not depend on the cwd: here the .env lives in one directory and the server runs in another."""
    with tempfile.TemporaryDirectory() as conf_dir, tempfile.TemporaryDirectory() as elsewhere:
        env_file = os.path.join(conf_dir, "my.env")
        with open(env_file, "w") as f:
            f.write(_BOTH)
        env = dict(os.environ, PYTHONPATH=REPO, MULTICLOUD_MCP_ENV_FILE=env_file)
        for key in list(env):
            if key.startswith(("GCP_ENV", "AWS_ENV", "GCP_DEFAULT", "AWS_DEFAULT", "GCP_ENVIRONMENTS", "AWS_ENVIRONMENTS")):
                del env[key]
        result = subprocess.run(
            [PYTHON, "-c", "import server; print(len(server.mcp._tool_manager._tools))"],
            cwd=elsewhere, env=env, capture_output=True, text=True, timeout=15,
        )
        assert result.returncode == 0, result.stderr
        assert int(result.stdout.strip()) == 38


def test_default_env_file_is_next_to_the_code_even_when_cwd_has_a_different_env():
    """With no override, the file used is <repo>/.env, NOT ./.env of whatever directory the client uses."""
    with tempfile.TemporaryDirectory() as tmp_dir:
        with open(os.path.join(tmp_dir, ".env"), "w") as f:
            f.write(_BOTH)   # a decoy in the cwd that must be ignored
        env = dict(os.environ, PYTHONPATH=REPO)
        env.pop("MULTICLOUD_MCP_ENV_FILE", None)
        result = subprocess.run(
            [PYTHON, "-c", "import config; print(config.default_env_path())"],
            cwd=tmp_dir, env=env, capture_output=True, text=True, timeout=15,
        )
        assert result.returncode == 0, result.stderr
        assert result.stdout.strip() == os.path.join(REPO, ".env")
