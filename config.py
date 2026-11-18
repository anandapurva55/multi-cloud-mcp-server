"""
Configuration loading for the multi-cloud MCP server.

Reads .env (written by setup_wizard.py) into typed environment registries.
Supports an arbitrary number of named environments per provider.

The .env file is located next to this file, NOT in the current directory: MCP clients
(Claude Desktop, VS Code, ...) launch servers from arbitrary working directories.
Set MULTICLOUD_MCP_ENV_FILE to use a different file. Real environment variables
(e.g. an "env" block in the MCP client config) take precedence over the file.
"""
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, Literal, Optional

GCPAuthMode = Literal["adc", "key_file"]
AWSAuthMode = Literal["profile", "keys"]

GCP_AUTH_MODES = ("adc", "key_file")
AWS_AUTH_MODES = ("profile", "keys")

ENV_FILE_VAR = "MULTICLOUD_MCP_ENV_FILE"
DEFAULT_ENV_FILE = Path(__file__).resolve().parent / ".env"

# BigQuery bytes-billed ceiling per query unless an environment overrides it (10 GB).
DEFAULT_MAX_BYTES_BILLED = 10_000_000_000


class ConfigError(ValueError):
    """The .env configuration is invalid. The message says what to fix."""


def default_env_path() -> str:
    return os.environ.get(ENV_FILE_VAR) or str(DEFAULT_ENV_FILE)


def slug(name: str) -> str:
    return re.sub(r"[^A-Za-z0-9]", "_", name).upper()


@dataclass
class GCPEnvironment:
    name: str
    project_id: str
    region: str = "us-central1"
    auth_mode: GCPAuthMode = "adc"
    credentials_path: Optional[str] = None
    max_bytes_billed: int = DEFAULT_MAX_BYTES_BILLED


@dataclass
class AWSEnvironment:
    name: str
    region: str
    auth_mode: AWSAuthMode = "profile"
    profile: Optional[str] = None
    access_key_id: Optional[str] = None
    secret_access_key: Optional[str] = None
    session_token: Optional[str] = None
    athena_output_location: Optional[str] = None
    athena_workgroup: Optional[str] = None


@dataclass
class AppConfig:
    gcp_environments: Dict[str, GCPEnvironment] = field(default_factory=dict)
    aws_environments: Dict[str, AWSEnvironment] = field(default_factory=dict)
    gcp_default: Optional[str] = None
    aws_default: Optional[str] = None

    @property
    def gcp_enabled(self) -> bool:
        return bool(self.gcp_environments)

    @property
    def aws_enabled(self) -> bool:
        return bool(self.aws_environments)

    def resolve_gcp(self, env: Optional[str] = None) -> GCPEnvironment:
        env = env or self.gcp_default
        if env not in self.gcp_environments:
            available = ", ".join(self.gcp_environments) or "(none, run setup_wizard.py)"
            raise ValueError(f"Unknown GCP environment '{env}'. Configured: {available}")
        return self.gcp_environments[env]

    def resolve_aws(self, env: Optional[str] = None) -> AWSEnvironment:
        env = env or self.aws_default
        if env not in self.aws_environments:
            available = ", ".join(self.aws_environments) or "(none, run setup_wizard.py)"
            raise ValueError(f"Unknown AWS environment '{env}'. Configured: {available}")
        return self.aws_environments[env]


def _names(raw: str) -> list:
    return [n.strip() for n in raw.split(",") if n.strip()]


def _check_unique_slugs(provider: str, names: list) -> None:
    seen = {}
    for name in names:
        s = slug(name)
        if s in seen and seen[s] != name:
            raise ConfigError(
                f"{provider} environments '{seen[s]}' and '{name}' both map to the key prefix "
                f"'{s}' and would overwrite each other. Rename one of them."
            )
        seen[s] = name


def load_config(path: Optional[str] = None) -> AppConfig:
    """Load and validate configuration. Raises ConfigError on invalid settings.
    Does not modify os.environ; real environment variables override the file."""
    from dotenv import dotenv_values
    path = path or default_env_path()
    # interpolate=False: python-dotenv would otherwise expand '${VAR}' even inside single quotes,
    # silently corrupting secrets/paths that contain '$'.
    file_values = dotenv_values(path, interpolate=False) if os.path.isfile(path) else {}

    def get(key: str, default: Optional[str] = None) -> Optional[str]:
        if key in os.environ:
            return os.environ[key]
        value = file_values.get(key)
        return default if value is None else value

    cfg = AppConfig()

    gcp_names = _names(get("GCP_ENVIRONMENTS", ""))
    _check_unique_slugs("GCP", gcp_names)
    for name in gcp_names:
        s = slug(name)
        project_id = get(f"GCP_ENV_{s}_PROJECT_ID")
        if not project_id:
            raise ConfigError(f"GCP environment '{name}' is listed in GCP_ENVIRONMENTS but GCP_ENV_{s}_PROJECT_ID is not set.")
        auth_mode = get(f"GCP_ENV_{s}_AUTH_MODE", "adc")
        if auth_mode not in GCP_AUTH_MODES:
            raise ConfigError(f"GCP_ENV_{s}_AUTH_MODE='{auth_mode}' is invalid; use one of: {', '.join(GCP_AUTH_MODES)}.")
        credentials_path = get(f"GCP_ENV_{s}_CREDENTIALS_PATH") or None
        if auth_mode == "key_file" and not credentials_path:
            raise ConfigError(f"GCP environment '{name}' uses auth_mode=key_file but GCP_ENV_{s}_CREDENTIALS_PATH is not set.")
        raw_cap = get(f"GCP_ENV_{s}_MAX_BYTES_BILLED") or ""
        try:
            max_bytes = int(raw_cap) if raw_cap else DEFAULT_MAX_BYTES_BILLED
        except ValueError:
            raise ConfigError(f"GCP_ENV_{s}_MAX_BYTES_BILLED='{raw_cap}' must be an integer number of bytes.")
        if max_bytes <= 0:
            raise ConfigError(f"GCP_ENV_{s}_MAX_BYTES_BILLED must be greater than 0.")
        cfg.gcp_environments[name] = GCPEnvironment(
            name=name,
            project_id=project_id,
            region=get(f"GCP_ENV_{s}_REGION", "us-central1"),
            auth_mode=auth_mode,
            credentials_path=credentials_path,
            max_bytes_billed=max_bytes,
        )
    cfg.gcp_default = get("GCP_DEFAULT_ENVIRONMENT") or next(iter(cfg.gcp_environments), None)
    if cfg.gcp_environments and cfg.gcp_default not in cfg.gcp_environments:
        raise ConfigError(
            f"GCP_DEFAULT_ENVIRONMENT='{cfg.gcp_default}' is not one of the configured GCP environments "
            f"({', '.join(cfg.gcp_environments)})."
        )

    aws_names = _names(get("AWS_ENVIRONMENTS", ""))
    _check_unique_slugs("AWS", aws_names)
    for name in aws_names:
        s = slug(name)
        region = get(f"AWS_ENV_{s}_REGION")
        if not region:
            raise ConfigError(f"AWS environment '{name}' is listed in AWS_ENVIRONMENTS but AWS_ENV_{s}_REGION is not set.")
        auth_mode = get(f"AWS_ENV_{s}_AUTH_MODE", "profile")
        if auth_mode not in AWS_AUTH_MODES:
            raise ConfigError(f"AWS_ENV_{s}_AUTH_MODE='{auth_mode}' is invalid; use one of: {', '.join(AWS_AUTH_MODES)}.")
        access_key_id = get(f"AWS_ENV_{s}_ACCESS_KEY_ID") or None
        secret_access_key = get(f"AWS_ENV_{s}_SECRET_ACCESS_KEY") or None
        if auth_mode == "keys" and not (access_key_id and secret_access_key):
            # boto3 would otherwise silently fall back to the default credential chain,
            # which may be a different account than the one intended.
            raise ConfigError(
                f"AWS environment '{name}' uses auth_mode=keys but AWS_ENV_{s}_ACCESS_KEY_ID and/or "
                f"AWS_ENV_{s}_SECRET_ACCESS_KEY is empty."
            )
        cfg.aws_environments[name] = AWSEnvironment(
            name=name,
            region=region,
            auth_mode=auth_mode,
            profile=get(f"AWS_ENV_{s}_PROFILE") or None,
            access_key_id=access_key_id,
            secret_access_key=secret_access_key,
            session_token=get(f"AWS_ENV_{s}_SESSION_TOKEN") or None,
            athena_output_location=get(f"AWS_ENV_{s}_ATHENA_OUTPUT_LOCATION") or None,
            athena_workgroup=get(f"AWS_ENV_{s}_ATHENA_WORKGROUP") or None,
        )
    cfg.aws_default = get("AWS_DEFAULT_ENVIRONMENT") or next(iter(cfg.aws_environments), None)
    if cfg.aws_environments and cfg.aws_default not in cfg.aws_environments:
        raise ConfigError(
            f"AWS_DEFAULT_ENVIRONMENT='{cfg.aws_default}' is not one of the configured AWS environments "
            f"({', '.join(cfg.aws_environments)})."
        )

    return cfg


_SAFE_VALUE = re.compile(r"[A-Za-z0-9_./:@%+=,-]*")


def _quote(value) -> str:
    """Render a .env value so python-dotenv reads back exactly what was written.
    Values with spaces, '#', '$', quotes, etc. are single-quoted (no interpolation)."""
    value = str(value)
    if _SAFE_VALUE.fullmatch(value):
        return value
    return "'" + value.replace("\\", "\\\\").replace("'", "\\'") + "'"


def render_env_file(cfg: AppConfig) -> str:
    lines = []

    lines.append("# ---- GCP ----")
    lines.append(f"GCP_ENVIRONMENTS={_quote(','.join(cfg.gcp_environments))}")
    lines.append(f"GCP_DEFAULT_ENVIRONMENT={_quote(cfg.gcp_default or '')}")
    lines.append("")
    for env in cfg.gcp_environments.values():
        s = slug(env.name)
        lines.append(f"GCP_ENV_{s}_PROJECT_ID={_quote(env.project_id)}")
        lines.append(f"GCP_ENV_{s}_REGION={_quote(env.region)}")
        lines.append(f"GCP_ENV_{s}_AUTH_MODE={_quote(env.auth_mode)}")
        if env.credentials_path:
            lines.append(f"GCP_ENV_{s}_CREDENTIALS_PATH={_quote(env.credentials_path)}")
        if env.max_bytes_billed != DEFAULT_MAX_BYTES_BILLED:
            lines.append(f"GCP_ENV_{s}_MAX_BYTES_BILLED={env.max_bytes_billed}")
        lines.append("")

    lines.append("# ---- AWS ----")
    lines.append(f"AWS_ENVIRONMENTS={_quote(','.join(cfg.aws_environments))}")
    lines.append(f"AWS_DEFAULT_ENVIRONMENT={_quote(cfg.aws_default or '')}")
    lines.append("")
    for env in cfg.aws_environments.values():
        s = slug(env.name)
        lines.append(f"AWS_ENV_{s}_REGION={_quote(env.region)}")
        lines.append(f"AWS_ENV_{s}_AUTH_MODE={_quote(env.auth_mode)}")
        if env.profile:
            lines.append(f"AWS_ENV_{s}_PROFILE={_quote(env.profile)}")
        if env.access_key_id:
            lines.append(f"AWS_ENV_{s}_ACCESS_KEY_ID={_quote(env.access_key_id)}")
        if env.secret_access_key:
            lines.append(f"AWS_ENV_{s}_SECRET_ACCESS_KEY={_quote(env.secret_access_key)}")
        if env.session_token:
            lines.append(f"AWS_ENV_{s}_SESSION_TOKEN={_quote(env.session_token)}")
        if env.athena_output_location:
            lines.append(f"AWS_ENV_{s}_ATHENA_OUTPUT_LOCATION={_quote(env.athena_output_location)}")
        if env.athena_workgroup:
            lines.append(f"AWS_ENV_{s}_ATHENA_WORKGROUP={_quote(env.athena_workgroup)}")
        lines.append("")

    return "\n".join(lines) + "\n"


# Keys the wizard owns and regenerates; every other line in an existing .env is kept.
_MANAGED_KEY = re.compile(r"^\s*(?:export\s+)?(GCP|AWS)_(ENVIRONMENTS|DEFAULT_ENVIRONMENT|ENV_\w+)\s*=")
_MANAGED_HEADER = re.compile(r"^#\s*---- (GCP|AWS|other \(preserved\)) ----\s*$")


def _unmanaged_lines(path: str) -> list:
    if not os.path.isfile(path):
        return []
    kept = []
    with open(path) as f:
        for line in f.read().splitlines():
            if _MANAGED_KEY.match(line) or _MANAGED_HEADER.match(line):
                continue
            kept.append(line)
    while kept and not kept[-1].strip():
        kept.pop()
    while kept and not kept[0].strip():
        kept.pop(0)
    return kept


def save_config(cfg: AppConfig, path: Optional[str] = None) -> None:
    """Write the environments to .env, preserving any hand-added lines already there."""
    path = path or default_env_path()
    extra = _unmanaged_lines(path)
    text = render_env_file(cfg)
    if extra:
        text += "\n# ---- other (preserved) ----\n" + "\n".join(extra) + "\n"
    # Create with 0600 from the start so secrets are never briefly world-readable.
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        f.write(text)
    os.chmod(path, 0o600)


try:
    CONFIG = load_config()
    CONFIG_ERROR: Optional[str] = None
except ConfigError as _e:
    CONFIG = AppConfig()
    CONFIG_ERROR = str(_e)
