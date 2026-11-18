"""
Tests for setup_wizard.py.

Pure functions (prompt, prompt_choice, prompt_yes_no) are tested in-process with
monkeypatched input(). The full interactive main() loop is tested via real subprocesses,
each with its own isolated temp directory as the working directory, so a wizard run's
.env write never touches the shared repo root while other test files' processes may be
running concurrently.
"""
import os
import subprocess
import sys
import tempfile

import pytest

import setup_wizard
from config import load_config

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PYTHON = os.path.join(REPO, ".venv", "bin", "python3")
if not os.path.isfile(PYTHON):
    PYTHON = sys.executable


# ─── pure function unit tests ───────────────────────────────────────────────

def _feed_input(monkeypatch, values):
    it = iter(values)
    monkeypatch.setattr("builtins.input", lambda *a, **k: next(it))


def test_prompt_uses_default_on_blank(monkeypatch):
    _feed_input(monkeypatch, [""])
    assert setup_wizard.prompt("Region", default="us-central1") == "us-central1"


def test_prompt_requires_value_when_no_default(monkeypatch, capsys):
    _feed_input(monkeypatch, ["", "actual-value"])
    result = setup_wizard.prompt("Project ID")
    assert result == "actual-value"
    assert "required" in capsys.readouterr().out


def test_prompt_choice_selects_by_index(monkeypatch):
    _feed_input(monkeypatch, ["2"])
    result = setup_wizard.prompt_choice("Pick one:", ["alpha", "beta", "gamma"])
    assert result == "beta"


def test_prompt_choice_rejects_invalid_then_accepts(monkeypatch, capsys):
    _feed_input(monkeypatch, ["9", "abc", "1"])
    result = setup_wizard.prompt_choice("Pick one:", ["alpha", "beta"])
    assert result == "alpha"
    assert "Invalid choice" in capsys.readouterr().out


@pytest.mark.parametrize("raw,expected", [("", True), ("y", True), ("Y", True), ("yes", True),
                                            ("n", False), ("no", False), ("anything", False)])
def test_prompt_yes_no_default_true(monkeypatch, raw, expected):
    _feed_input(monkeypatch, [raw])
    assert setup_wizard.prompt_yes_no("Continue?", default_yes=True) == expected


def test_prompt_yes_no_default_false_on_blank(monkeypatch):
    _feed_input(monkeypatch, [""])
    assert setup_wizard.prompt_yes_no("Continue?", default_yes=False) is False


# ─── full interactive flow, via isolated subprocess ─────────────────────────

def _run_wizard(stdin_text: str, cwd: str, extra_env: dict = None, timeout: float = 30):
    env = dict(os.environ)
    env["PYTHONPATH"] = REPO
    env["MULTICLOUD_MCP_ENV_FILE"] = os.path.join(cwd, ".env")
    for key in list(env):
        if key.startswith(("GCP_", "AWS_")):
            del env[key]
    if extra_env:
        env.update(extra_env)
    return subprocess.run(
        [PYTHON, os.path.join(REPO, "setup_wizard.py")],
        cwd=cwd,
        input=stdin_text,
        env=env,
        capture_output=True,
        text=True,
        timeout=timeout,
    )


def test_wizard_adds_gcp_environment_with_adc_and_saves():
    with tempfile.TemporaryDirectory() as tmp_dir:
        stdin = "1\n1\ndev\ntest-project\n\n1\n\n4\n"
        result = _run_wizard(stdin, cwd=tmp_dir)
        assert result.returncode == 0, result.stdout + result.stderr
        assert "Added GCP environment 'dev'." in result.stdout
        assert "(1 GCP, 0 AWS environment(s))." in result.stdout

        env_path = os.path.join(tmp_dir, ".env")
        assert os.path.isfile(env_path)
        assert (os.stat(env_path).st_mode & 0o777) == 0o600

        cfg = load_config(env_path)
        assert cfg.gcp_default == "dev"
        env = cfg.gcp_environments["dev"]
        assert env.project_id == "test-project"
        assert env.region == "us-central1"
        assert env.auth_mode == "adc"


def test_wizard_adds_aws_environment_with_keys_after_failed_verify_and_saves():
    with tempfile.TemporaryDirectory() as tmp_dir:
        # Provider AWS, name "prod", region, auth=keys, fake key/secret, no session token,
        # verify will fail fast (offline STS endpoint override below), answer "y" to save
        # anyway, decline Athena output location, then save and exit.
        stdin = "1\n2\nprod\nus-east-1\n2\nAKIAFAKEEXAMPLE\nfakesecretvalue\n\ny\nn\n4\n"
        result = _run_wizard(
            stdin, cwd=tmp_dir,
            extra_env={"AWS_ENDPOINT_URL_STS": "http://127.0.0.1:1"},
        )
        assert result.returncode == 0, result.stdout + result.stderr
        assert "FAILED to verify credentials" in result.stdout
        assert "Added AWS environment 'prod'." in result.stdout

        env_path = os.path.join(tmp_dir, ".env")
        cfg = load_config(env_path)
        assert cfg.aws_default == "prod"
        env = cfg.aws_environments["prod"]
        assert env.region == "us-east-1"
        assert env.auth_mode == "keys"
        assert env.access_key_id == "AKIAFAKEEXAMPLE"
        assert env.secret_access_key == "fakesecretvalue"
        assert env.athena_output_location is None


def test_wizard_exit_without_saving_leaves_no_env_file():
    with tempfile.TemporaryDirectory() as tmp_dir:
        stdin = "5\n"
        result = _run_wizard(stdin, cwd=tmp_dir)
        assert result.returncode == 0, result.stdout + result.stderr
        assert "Discarded changes." in result.stdout
        assert not os.path.isfile(os.path.join(tmp_dir, ".env"))


def test_wizard_is_idempotent_reruns_load_existing_env_and_can_add_more():
    with tempfile.TemporaryDirectory() as tmp_dir:
        first_stdin = "1\n1\ndev\ntest-project\n\n1\n\n4\n"
        first = _run_wizard(first_stdin, cwd=tmp_dir)
        assert first.returncode == 0, first.stdout + first.stderr

        second_stdin = "1\n1\nprod\nother-project\n\n1\n\n4\n"
        second = _run_wizard(second_stdin, cwd=tmp_dir)
        assert second.returncode == 0, second.stdout + second.stderr
        assert "Loaded 1 GCP environment(s): dev (default: dev)" in second.stdout
        assert "Added GCP environment 'prod'." in second.stdout

        cfg = load_config(os.path.join(tmp_dir, ".env"))
        assert set(cfg.gcp_environments) == {"dev", "prod"}
        assert cfg.gcp_default == "dev"


def test_wizard_remove_environment_flow():
    with tempfile.TemporaryDirectory() as tmp_dir:
        setup_stdin = "1\n1\ndev\ntest-project\n\n1\n\n4\n"
        setup_result = _run_wizard(setup_stdin, cwd=tmp_dir)
        assert setup_result.returncode == 0, setup_result.stdout + setup_result.stderr

        remove_stdin = "2\n1\n4\n"
        result = _run_wizard(remove_stdin, cwd=tmp_dir)
        assert result.returncode == 0, result.stdout + result.stderr
        assert "Removed 'dev'." in result.stdout

        cfg = load_config(os.path.join(tmp_dir, ".env"))
        assert cfg.gcp_environments == {}
        assert cfg.gcp_default is None


def test_wizard_set_default_environment_flow():
    with tempfile.TemporaryDirectory() as tmp_dir:
        setup_stdin = "1\n1\ndev\ntest-project\n\n1\n\n1\n1\nprod\nother-project\n\n1\n\n4\n"
        setup_result = _run_wizard(setup_stdin, cwd=tmp_dir)
        assert setup_result.returncode == 0, setup_result.stdout + setup_result.stderr
        cfg = load_config(os.path.join(tmp_dir, ".env"))
        assert set(cfg.gcp_environments) == {"dev", "prod"}
        assert cfg.gcp_default == "dev"

        set_default_stdin = "3\n1\n2\n4\n"
        result = _run_wizard(set_default_stdin, cwd=tmp_dir)
        assert result.returncode == 0, result.stdout + result.stderr
        assert "Default GCP environment set to 'prod'." in result.stdout

        cfg = load_config(os.path.join(tmp_dir, ".env"))
        assert cfg.gcp_default == "prod"


# ─── name collisions, validation, and preserving hand-added settings ───────

def test_prompt_new_name_rejects_slug_collision_then_accepts(monkeypatch, capsys):
    # 'client_a' collides with existing 'client-a' (both become CLIENT_A in .env)
    _feed_input(monkeypatch, ["client_a", "client-b"])
    name = setup_wizard.prompt_new_name("Name", {"client-a": object()})
    assert name == "client-b"
    assert "collide" in capsys.readouterr().out


def test_prompt_new_name_rejects_exact_duplicate_and_commas(monkeypatch, capsys):
    _feed_input(monkeypatch, ["dev", "a,b", "qa"])
    assert setup_wizard.prompt_new_name("Name", {"dev": object()}) == "qa"
    out = capsys.readouterr().out
    assert "already exists" in out and "commas" in out


def test_prompt_positive_int(monkeypatch, capsys):
    _feed_input(monkeypatch, ["abc", "0", "-3", "42"])
    assert setup_wizard.prompt_positive_int("Max", default=7) == 42
    _feed_input(monkeypatch, [""])
    assert setup_wizard.prompt_positive_int("Max", default=7) == 7


def test_wizard_refuses_colliding_names_end_to_end():
    with tempfile.TemporaryDirectory() as tmp_dir:
        # add 'client-a', then try 'client_a' (rejected), then 'client-b'; save
        first = "1\n1\nclient-a\np1\n\n1\n\n"
        second = "1\n1\nclient_a\nclient-b\np2\n\n1\n\n"
        result = _run_wizard(first + second + "4\n", cwd=tmp_dir)
        assert result.returncode == 0, result.stdout + result.stderr
        assert "would collide with existing environment 'client-a'" in result.stdout
        cfg = load_config(os.path.join(tmp_dir, ".env"))
        assert set(cfg.gcp_environments) == {"client-a", "client-b"}
        assert cfg.gcp_environments["client-a"].project_id == "p1"   # not overwritten
        assert cfg.gcp_environments["client-b"].project_id == "p2"


def test_wizard_keeps_custom_max_bytes_and_hand_added_lines():
    with tempfile.TemporaryDirectory() as tmp_dir:
        env_path = os.path.join(tmp_dir, ".env")
        with open(env_path, "w") as f:
            f.write("HTTPS_PROXY=http://proxy.example:8080\n")
        result = _run_wizard("1\n1\ndev\np\n\n1\n5000000000\n4\n", cwd=tmp_dir)
        assert result.returncode == 0, result.stdout + result.stderr
        text = open(env_path).read()
        assert "HTTPS_PROXY=http://proxy.example:8080" in text   # was lost before (file rewritten from scratch)
        assert load_config(env_path).gcp_environments["dev"].max_bytes_billed == 5_000_000_000


def test_wizard_reports_an_invalid_existing_env_file_instead_of_crashing():
    with tempfile.TemporaryDirectory() as tmp_dir:
        with open(os.path.join(tmp_dir, ".env"), "w") as f:
            f.write("GCP_ENVIRONMENTS=dev\nGCP_ENV_DEV_PROJECT_ID=p\nGCP_ENV_DEV_AUTH_MODE=nope\n")
        result = _run_wizard("5\n", cwd=tmp_dir)
        assert result.returncode == 1
        assert "is invalid" in result.stdout and "AUTH_MODE" in result.stdout
        assert "Traceback" not in result.stderr
