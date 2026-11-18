"""
Tests for config.py: slug(), the AppConfig dataclass and its resolve_* methods,
load_config()/render_env_file()/save_config() round-tripping, and graceful skipping
of environments with missing required fields.

These tests never rely on or mutate the module-level config.CONFIG singleton, and
never call load_config()/save_config() with the default ".env" path: every call
passes an explicit path built from pytest's tmp_path fixture so the real repo-root
.env (if any) can never be read or written by this file.
"""
import os
import stat

import pytest

from config import (
    CONFIG_ERROR,
    ConfigError,
    DEFAULT_ENV_FILE,
    DEFAULT_MAX_BYTES_BILLED,
    ENV_FILE_VAR,
    AppConfig,
    AWSEnvironment,
    GCPEnvironment,
    load_config,
    render_env_file,
    default_env_path,
    save_config,
    slug,
)


@pytest.fixture(autouse=True)
def _isolate_dotenv_vars(monkeypatch):
    """load_config() uses python-dotenv's load_dotenv(path, override=True), which only
    overrides keys that are present in the given file: it never unsets a GCP_*/AWS_*
    key that a previous test's load_config() call left behind in os.environ (dotenv
    mutates the real process environment). Without this, tests that call load_config
    would leak state into each other depending on run order. This clears any such
    leftovers before every test in this file, whether or not that test touches
    load_config, so ordering never matters."""
    for key in list(os.environ):
        if key.startswith("GCP_") or key.startswith("AWS_"):
            monkeypatch.delenv(key, raising=False)
    yield


# ---------------------------------------------------------------------------
# slug()
# ---------------------------------------------------------------------------

def test_slug_underscore_name():
    assert slug("dev_qa") == "DEV_QA"


def test_slug_hyphenated_name():
    assert slug("client-a-prod") == "CLIENT_A_PROD"


def test_slug_spaces_become_underscores():
    assert slug("my env") == "MY_ENV"


# ---------------------------------------------------------------------------
# AppConfig.resolve_gcp / resolve_aws
# ---------------------------------------------------------------------------

def test_resolve_gcp_none_returns_default():
    cfg = AppConfig()
    cfg.gcp_environments["a"] = GCPEnvironment(name="a", project_id="proj-a")
    cfg.gcp_environments["b"] = GCPEnvironment(name="b", project_id="proj-b")
    cfg.gcp_default = "b"

    resolved = cfg.resolve_gcp(None)
    assert resolved.name == "b"
    assert resolved.project_id == "proj-b"


def test_resolve_gcp_explicit_name_returns_that_environment():
    cfg = AppConfig()
    cfg.gcp_environments["a"] = GCPEnvironment(name="a", project_id="proj-a")
    cfg.gcp_environments["b"] = GCPEnvironment(name="b", project_id="proj-b")
    cfg.gcp_default = "a"

    resolved = cfg.resolve_gcp("b")
    assert resolved.name == "b"
    assert resolved.project_id == "proj-b"


def test_resolve_gcp_unknown_name_raises_with_configured_list():
    cfg = AppConfig()
    cfg.gcp_environments["a"] = GCPEnvironment(name="a", project_id="proj-a")
    cfg.gcp_environments["b"] = GCPEnvironment(name="b", project_id="proj-b")
    cfg.gcp_default = "a"

    with pytest.raises(ValueError) as excinfo:
        cfg.resolve_gcp("nonexistent")
    msg = str(excinfo.value)
    assert "Unknown GCP environment 'nonexistent'" in msg
    assert "Configured:" in msg
    assert "a" in msg
    assert "b" in msg


def test_resolve_gcp_empty_registry_raises_setup_wizard_hint():
    cfg = AppConfig()
    with pytest.raises(ValueError) as excinfo:
        cfg.resolve_gcp()
    msg = str(excinfo.value)
    assert "Unknown GCP environment 'None'" in msg
    assert "(none, run setup_wizard.py)" in msg


def test_resolve_aws_none_returns_default():
    cfg = AppConfig()
    cfg.aws_environments["a"] = AWSEnvironment(name="a", region="us-east-1")
    cfg.aws_environments["b"] = AWSEnvironment(name="b", region="us-west-2")
    cfg.aws_default = "b"

    resolved = cfg.resolve_aws(None)
    assert resolved.name == "b"
    assert resolved.region == "us-west-2"


def test_resolve_aws_explicit_name_returns_that_environment():
    cfg = AppConfig()
    cfg.aws_environments["a"] = AWSEnvironment(name="a", region="us-east-1")
    cfg.aws_environments["b"] = AWSEnvironment(name="b", region="us-west-2")
    cfg.aws_default = "a"

    resolved = cfg.resolve_aws("b")
    assert resolved.name == "b"
    assert resolved.region == "us-west-2"


def test_resolve_aws_unknown_name_raises_with_configured_list():
    cfg = AppConfig()
    cfg.aws_environments["a"] = AWSEnvironment(name="a", region="us-east-1")
    cfg.aws_environments["b"] = AWSEnvironment(name="b", region="us-west-2")
    cfg.aws_default = "a"

    with pytest.raises(ValueError) as excinfo:
        cfg.resolve_aws("nonexistent")
    msg = str(excinfo.value)
    assert "Unknown AWS environment 'nonexistent'" in msg
    assert "Configured:" in msg
    assert "a" in msg
    assert "b" in msg


def test_resolve_aws_empty_registry_raises_setup_wizard_hint():
    cfg = AppConfig()
    with pytest.raises(ValueError) as excinfo:
        cfg.resolve_aws()
    msg = str(excinfo.value)
    assert "Unknown AWS environment 'None'" in msg
    assert "(none, run setup_wizard.py)" in msg


def test_gcp_enabled_and_aws_enabled_flags():
    empty = AppConfig()
    assert empty.gcp_enabled is False
    assert empty.aws_enabled is False

    cfg = AppConfig()
    cfg.gcp_environments["a"] = GCPEnvironment(name="a", project_id="proj-a")
    cfg.aws_environments["b"] = AWSEnvironment(name="b", region="us-east-1")
    assert cfg.gcp_enabled is True
    assert cfg.aws_enabled is True


# ---------------------------------------------------------------------------
# load_config / render_env_file round-trip
# ---------------------------------------------------------------------------

def _build_full_config() -> AppConfig:
    cfg = AppConfig()

    cfg.gcp_environments["dev"] = GCPEnvironment(
        name="dev",
        project_id="dev-project",
        region="us-central1",
        auth_mode="adc",
    )
    cfg.gcp_environments["client-a-prod"] = GCPEnvironment(
        name="client-a-prod",
        project_id="client-a-prod-project",
        region="europe-west1",
        auth_mode="key_file",
        credentials_path="/fake/path/to/creds.json",
    )
    cfg.gcp_default = "dev"

    cfg.aws_environments["staging"] = AWSEnvironment(
        name="staging",
        region="us-east-1",
        auth_mode="profile",
        profile="staging-profile",
    )
    cfg.aws_environments["prod_keys"] = AWSEnvironment(
        name="prod_keys",
        region="us-west-2",
        auth_mode="keys",
        access_key_id="AKIAFAKEKEYID",
        secret_access_key="fakeSecretAccessKey1234",
        session_token="fakeSessionToken5678",
        athena_output_location="s3://fake-athena-bucket/output/",
    )
    cfg.aws_default = "prod_keys"

    return cfg


def test_load_config_round_trips_all_fields(tmp_path):
    original = _build_full_config()
    env_text = render_env_file(original)

    env_file = tmp_path / ".env"
    env_file.write_text(env_text)

    loaded = load_config(str(env_file))

    # GCP defaults and environment set.
    assert loaded.gcp_default == "dev"
    assert set(loaded.gcp_environments) == {"dev", "client-a-prod"}

    dev = loaded.gcp_environments["dev"]
    assert dev.name == "dev"
    assert dev.project_id == "dev-project"
    assert dev.region == "us-central1"
    assert dev.auth_mode == "adc"
    assert dev.credentials_path is None

    client_a = loaded.gcp_environments["client-a-prod"]
    assert client_a.name == "client-a-prod"
    assert client_a.project_id == "client-a-prod-project"
    assert client_a.region == "europe-west1"
    assert client_a.auth_mode == "key_file"
    assert client_a.credentials_path == "/fake/path/to/creds.json"

    # AWS defaults and environment set.
    assert loaded.aws_default == "prod_keys"
    assert set(loaded.aws_environments) == {"staging", "prod_keys"}

    staging = loaded.aws_environments["staging"]
    assert staging.name == "staging"
    assert staging.region == "us-east-1"
    assert staging.auth_mode == "profile"
    assert staging.profile == "staging-profile"
    assert staging.access_key_id is None
    assert staging.secret_access_key is None
    assert staging.session_token is None
    assert staging.athena_output_location is None

    prod_keys = loaded.aws_environments["prod_keys"]
    assert prod_keys.name == "prod_keys"
    assert prod_keys.region == "us-west-2"
    assert prod_keys.auth_mode == "keys"
    assert prod_keys.access_key_id == "AKIAFAKEKEYID"
    assert prod_keys.secret_access_key == "fakeSecretAccessKey1234"
    assert prod_keys.session_token == "fakeSessionToken5678"
    assert prod_keys.athena_output_location == "s3://fake-athena-bucket/output/"


def test_save_config_writes_readable_round_trip_file(tmp_path):
    original = _build_full_config()
    env_file = tmp_path / ".env"

    save_config(original, str(env_file))

    loaded = load_config(str(env_file))
    assert loaded.gcp_default == "dev"
    assert loaded.aws_default == "prod_keys"
    assert loaded.gcp_environments["dev"].project_id == "dev-project"
    assert loaded.aws_environments["prod_keys"].access_key_id == "AKIAFAKEKEYID"


def test_save_config_sets_permissions_to_0600(tmp_path):
    cfg = _build_full_config()
    env_file = tmp_path / ".env"

    save_config(cfg, str(env_file))

    mode = stat.S_IMODE(os.stat(str(env_file)).st_mode)
    assert oct(mode) == "0o600"


# ---------------------------------------------------------------------------
# Graceful skipping of environments with missing required fields
# ---------------------------------------------------------------------------

def test_load_config_rejects_gcp_environment_missing_project_id(tmp_path):
    env_file = tmp_path / ".env"
    env_file.write_text(
        "GCP_ENVIRONMENTS=dev,broken\n"
        "GCP_DEFAULT_ENVIRONMENT=dev\n"
        "GCP_ENV_DEV_PROJECT_ID=dev-project\n"
        "GCP_ENV_DEV_REGION=us-central1\n"
        "GCP_ENV_DEV_AUTH_MODE=adc\n"
        # Note: no GCP_ENV_BROKEN_PROJECT_ID key at all for "broken".
        "GCP_ENV_BROKEN_REGION=us-central1\n"
    )

    # Previously the broken environment was silently dropped, so a typo meant tools quietly
    # pointed at fewer environments than the user thought. Now it is a startup error.
    with pytest.raises(ConfigError, match="GCP_ENV_BROKEN_PROJECT_ID"):
        load_config(str(env_file))


def test_load_config_rejects_aws_environment_missing_region(tmp_path):
    env_file = tmp_path / ".env"
    env_file.write_text(
        "AWS_ENVIRONMENTS=staging,broken\n"
        "AWS_DEFAULT_ENVIRONMENT=staging\n"
        "AWS_ENV_STAGING_REGION=us-east-1\n"
        "AWS_ENV_STAGING_AUTH_MODE=profile\n"
        "AWS_ENV_STAGING_PROFILE=staging-profile\n"
        # Note: no AWS_ENV_BROKEN_REGION key at all for "broken".
        "AWS_ENV_BROKEN_AUTH_MODE=profile\n"
    )

    with pytest.raises(ConfigError, match="AWS_ENV_BROKEN_REGION"):
        load_config(str(env_file))


def test_load_config_falls_back_to_first_environment_when_no_default_set(tmp_path):
    env_file = tmp_path / ".env"
    env_file.write_text(
        "GCP_ENVIRONMENTS=only-one\n"
        "GCP_ENV_ONLY_ONE_PROJECT_ID=only-one-project\n"
        "AWS_ENVIRONMENTS=only-aws\n"
        "AWS_ENV_ONLY_AWS_REGION=ap-south-1\n"
    )

    loaded = load_config(str(env_file))

    assert loaded.gcp_default == "only-one"
    assert loaded.aws_default == "only-aws"


def test_load_config_empty_file_produces_empty_registries(tmp_path):
    env_file = tmp_path / ".env"
    env_file.write_text("")

    loaded = load_config(str(env_file))

    assert loaded.gcp_environments == {}
    assert loaded.aws_environments == {}
    assert loaded.gcp_default is None
    assert loaded.aws_default is None
    assert loaded.gcp_enabled is False
    assert loaded.aws_enabled is False


# ---------------------------------------------------------------------------
# validation (typos used to silently fall back to ADC / the default AWS credential chain)
# ---------------------------------------------------------------------------

def _gcp(extra="", env="dev"):
    return f"GCP_ENVIRONMENTS={env}\nGCP_ENV_{env.upper()}_PROJECT_ID=p\n{extra}"


def _aws(extra="", env="dev"):
    return f"AWS_ENVIRONMENTS={env}\nAWS_ENV_{env.upper()}_REGION=us-east-1\n{extra}"


def _load(tmp_path, text):
    f = tmp_path / ".env"
    f.write_text(text)
    return load_config(str(f))


def test_invalid_gcp_auth_mode_is_rejected(tmp_path):
    with pytest.raises(ConfigError, match="GCP_ENV_DEV_AUTH_MODE='keyfile' is invalid"):
        _load(tmp_path, _gcp("GCP_ENV_DEV_AUTH_MODE=keyfile\n"))


def test_invalid_aws_auth_mode_is_rejected(tmp_path):
    with pytest.raises(ConfigError, match="AWS_ENV_DEV_AUTH_MODE='profle' is invalid"):
        _load(tmp_path, _aws("AWS_ENV_DEV_AUTH_MODE=profle\n"))


def test_gcp_key_file_mode_requires_credentials_path(tmp_path):
    with pytest.raises(ConfigError, match="CREDENTIALS_PATH is not set"):
        _load(tmp_path, _gcp("GCP_ENV_DEV_AUTH_MODE=key_file\n"))


def test_aws_keys_mode_with_empty_keys_is_rejected(tmp_path):
    # boto3 would silently fall back to the default credential chain (possibly another account)
    with pytest.raises(ConfigError, match="auth_mode=keys"):
        _load(tmp_path, _aws("AWS_ENV_DEV_AUTH_MODE=keys\nAWS_ENV_DEV_ACCESS_KEY_ID=AKIA\nAWS_ENV_DEV_SECRET_ACCESS_KEY=\n"))


def test_default_environment_must_exist(tmp_path):
    with pytest.raises(ConfigError, match="GCP_DEFAULT_ENVIRONMENT='prod' is not one of"):
        _load(tmp_path, _gcp("GCP_DEFAULT_ENVIRONMENT=prod\n"))
    with pytest.raises(ConfigError, match="AWS_DEFAULT_ENVIRONMENT='prod' is not one of"):
        _load(tmp_path, _aws("AWS_DEFAULT_ENVIRONMENT=prod\n"))


@pytest.mark.parametrize("value", ["abc", "0", "-5", "1.5"])
def test_invalid_max_bytes_billed_is_rejected(tmp_path, value):
    with pytest.raises(ConfigError, match="MAX_BYTES_BILLED"):
        _load(tmp_path, _gcp(f"GCP_ENV_DEV_MAX_BYTES_BILLED={value}\n"))


def test_max_bytes_billed_default_and_override(tmp_path):
    assert _load(tmp_path, _gcp()).gcp_environments["dev"].max_bytes_billed == DEFAULT_MAX_BYTES_BILLED
    assert _load(tmp_path, _gcp("GCP_ENV_DEV_MAX_BYTES_BILLED=123456\n")).gcp_environments["dev"].max_bytes_billed == 123456


def test_athena_workgroup_is_loaded(tmp_path):
    cfg = _load(tmp_path, _aws("AWS_ENV_DEV_ATHENA_WORKGROUP=capped\n"))
    assert cfg.aws_environments["dev"].athena_workgroup == "capped"


def test_colliding_environment_names_are_rejected(tmp_path):
    # 'client-a' and 'client_a' both become CLIENT_A, so one would silently overwrite the other
    with pytest.raises(ConfigError, match="both map to the key prefix 'CLIENT_A'"):
        _load(tmp_path, "GCP_ENVIRONMENTS=client-a,client_a\nGCP_ENV_CLIENT_A_PROJECT_ID=p\n")


def test_missing_env_file_is_an_empty_config_not_an_error(tmp_path):
    cfg = load_config(str(tmp_path / "does-not-exist.env"))
    assert not cfg.gcp_enabled and not cfg.aws_enabled


# ---------------------------------------------------------------------------
# where .env lives, and how it combines with real environment variables
# ---------------------------------------------------------------------------

def test_default_env_path_is_anchored_to_the_repo_not_the_working_directory(monkeypatch, tmp_path):
    monkeypatch.delenv(ENV_FILE_VAR, raising=False)
    monkeypatch.chdir(tmp_path)
    assert default_env_path() == str(DEFAULT_ENV_FILE)
    assert os.path.isabs(default_env_path())
    assert os.path.dirname(default_env_path()) == os.path.dirname(os.path.abspath(__import__("config").__file__))


def test_env_var_overrides_the_env_file_location(monkeypatch, tmp_path):
    monkeypatch.setenv(ENV_FILE_VAR, str(tmp_path / "custom.env"))
    assert default_env_path() == str(tmp_path / "custom.env")


def test_load_config_without_path_reads_the_override_file(monkeypatch, tmp_path):
    f = tmp_path / "custom.env"
    f.write_text(_gcp())
    monkeypatch.setenv(ENV_FILE_VAR, str(f))
    assert load_config().gcp_environments["dev"].project_id == "p"


def test_real_environment_variables_override_the_file(monkeypatch, tmp_path):
    # e.g. an "env" block in the MCP client config should win over a stale .env
    monkeypatch.setenv("GCP_ENV_DEV_PROJECT_ID", "from-client-config")
    assert _load(tmp_path, _gcp()).gcp_environments["dev"].project_id == "from-client-config"


def test_load_config_does_not_mutate_os_environ(tmp_path):
    before = dict(os.environ)
    _load(tmp_path, _gcp())
    assert dict(os.environ) == before


def test_module_level_config_error_is_none_for_a_valid_environment():
    assert CONFIG_ERROR is None


# ---------------------------------------------------------------------------
# writing .env: quoting round-trip, preserved lines, permissions
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("value", [
    "/Users/me/My Keys/sa key.json",    # spaces
    "/tmp/a#b.json",                    # '#' would start a comment if unquoted
    "pa$$word${HOME}",                  # '$' / ${} must not be interpolated
    "it's",                             # single quote
    'say "hi"',                         # double quote
    "back\\slash",                       # backslash
    "trailing space ",
    "plain-value_1.2/x:y@z",
])
def test_env_file_values_round_trip_exactly(tmp_path, value):
    cfg = AppConfig()
    cfg.gcp_environments["dev"] = GCPEnvironment(
        name="dev", project_id="p", auth_mode="key_file", credentials_path=value)
    cfg.gcp_default = "dev"
    path = tmp_path / ".env"
    save_config(cfg, str(path))
    assert load_config(str(path)).gcp_environments["dev"].credentials_path == value


def test_aws_secret_with_special_characters_round_trips(tmp_path):
    secret = "wJalr/XUtn+FEMI#K7MDENG$bPxRfiCY'EXAMPLE"
    cfg = AppConfig()
    cfg.aws_environments["dev"] = AWSEnvironment(
        name="dev", region="us-east-1", auth_mode="keys", access_key_id="AKIAX", secret_access_key=secret)
    cfg.aws_default = "dev"
    path = tmp_path / ".env"
    save_config(cfg, str(path))
    assert load_config(str(path)).aws_environments["dev"].secret_access_key == secret


def test_new_settings_round_trip(tmp_path):
    cfg = AppConfig()
    cfg.gcp_environments["dev"] = GCPEnvironment(name="dev", project_id="p", max_bytes_billed=42)
    cfg.aws_environments["dev"] = AWSEnvironment(name="dev", region="r", athena_workgroup="wg",
                                                  athena_output_location="s3://b/o/")
    cfg.gcp_default = cfg.aws_default = "dev"
    path = tmp_path / ".env"
    save_config(cfg, str(path))
    loaded = load_config(str(path))
    assert loaded.gcp_environments["dev"].max_bytes_billed == 42
    assert loaded.aws_environments["dev"].athena_workgroup == "wg"
    assert loaded.aws_environments["dev"].athena_output_location == "s3://b/o/"


def test_save_config_preserves_hand_added_lines_and_does_not_duplicate_them(tmp_path):
    path = tmp_path / ".env"
    path.write_text(
        "# my notes\nHTTPS_PROXY=http://proxy:8080\nGCP_ENVIRONMENTS=old\nGCP_ENV_OLD_PROJECT_ID=old-p\n"
        "AWS_ENV_OLD_REGION=us-west-2\nEXTRA_FLAG=1\n"
    )
    cfg = AppConfig()
    cfg.gcp_environments["new"] = GCPEnvironment(name="new", project_id="new-p")
    cfg.gcp_default = "new"
    save_config(cfg, str(path))
    save_config(cfg, str(path))  # re-saving must be stable
    text = path.read_text()
    assert "HTTPS_PROXY=http://proxy:8080" in text and "EXTRA_FLAG=1" in text and "# my notes" in text
    assert text.count("HTTPS_PROXY") == 1 and text.count("# ---- other (preserved) ----") == 1
    assert "old-p" not in text and "AWS_ENV_OLD_REGION" not in text  # managed keys are regenerated
    assert load_config(str(path)).gcp_environments["new"].project_id == "new-p"


def test_save_config_creates_file_with_0600_from_the_start(tmp_path, monkeypatch):
    seen = {}
    real_open = os.open

    def spy(path, flags, mode=0o777, *a, **k):
        if str(path).endswith("secret.env"):
            seen["mode"] = mode
        return real_open(path, flags, mode, *a, **k)

    monkeypatch.setattr(os, "open", spy)
    save_config(AppConfig(), str(tmp_path / "secret.env"))
    assert seen["mode"] == 0o600
    assert stat.S_IMODE(os.stat(tmp_path / "secret.env").st_mode) == 0o600
