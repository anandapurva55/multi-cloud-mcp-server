"""
Interactive setup wizard for the multi-cloud MCP server.

Walks you through configuring one or more named GCP and/or AWS environments
and writes them to .env. Safe to re-run any time to add, edit, or remove
an environment, it reloads whatever is already in .env first.
"""
import getpass
import os
import sys

from config import (
    DEFAULT_MAX_BYTES_BILLED, AppConfig, AWSEnvironment, ConfigError, GCPEnvironment,
    default_env_path, load_config, save_config, slug,
)

ENV_FILE = default_env_path()


def prompt(label: str, default: str = None) -> str:
    suffix = f" [{default}]" if default else ""
    while True:
        value = input(f"{label}{suffix}: ").strip()
        if not value and default is not None:
            return default
        if value:
            return value
        print("  This field is required.")


def prompt_choice(label: str, options: list) -> str:
    print(label)
    for i, opt in enumerate(options, 1):
        print(f"  {i}) {opt}")
    while True:
        raw = input(f"Choose [1-{len(options)}]: ").strip()
        if raw.isdigit() and 1 <= int(raw) <= len(options):
            return options[int(raw) - 1]
        print("  Invalid choice.")


def prompt_positive_int(label: str, default: int) -> int:
    while True:
        raw = input(f"{label} [{default}]: ").strip()
        if not raw:
            return default
        if raw.isdigit() and int(raw) > 0:
            return int(raw)
        print("  Enter a whole number greater than 0.")


def prompt_yes_no(label: str, default_yes: bool = True) -> bool:
    suffix = "Y/n" if default_yes else "y/N"
    raw = input(f"{label} [{suffix}]: ").strip().lower()
    if not raw:
        return default_yes
    return raw.startswith("y")


def prompt_new_name(label: str, existing: dict) -> str:
    """Ask for an environment name that is valid and cannot collide with an existing one.
    Names are stored as .env key prefixes via slug(), so 'client-a' and 'client_a' would
    silently overwrite each other; reject them up front."""
    taken = {slug(n): n for n in existing}
    while True:
        name = prompt(label)
        if "," in name:
            print("  Names cannot contain commas.")
        elif name in existing:
            print(f"  '{name}' already exists. Choose a different name or edit it from the main menu.")
        elif slug(name) in taken:
            print(f"  '{name}' would collide with existing environment '{taken[slug(name)]}' "
                  f"(both become '{slug(name)}' in .env). Choose a different name.")
        else:
            return name


# ─── GCP flow ───────────────────────────────────────────────────────────────

def verify_gcp(gcp_env: GCPEnvironment):
    try:
        from providers.gcp.auth import get_credentials
    except ImportError:
        print("  (skipping verification, google-auth not installed yet; run pip install -r requirements.txt)")
        return
    try:
        get_credentials(gcp_env)
        print("  Auth OK.")
    except Exception as e:
        print(f"  WARNING: could not obtain credentials: {e}")
        return

    try:
        from google.cloud import bigquery
        client = bigquery.Client(project=gcp_env.project_id, credentials=get_credentials(gcp_env))
        list(client.list_datasets(max_results=1))
        print("  BigQuery smoke test OK.")
    except Exception as e:
        print(f"  (non-fatal) BigQuery smoke test failed, this is fine if this project doesn't use BigQuery: {e}")


def add_gcp_environment(cfg: AppConfig):
    print("\n--- Add GCP environment ---")
    name = prompt_new_name("Environment name (e.g. 'dev', 'prod', 'client-a-prod')", cfg.gcp_environments)

    project_id = prompt("GCP project ID")
    region = prompt("Region", default="us-central1")
    auth_choice = prompt_choice("Auth method:", [
        "Application Default Credentials (gcloud auth application-default login)",
        "Service account JSON key file",
    ])

    if auth_choice.startswith("Service account"):
        auth_mode = "key_file"
        while True:
            path = prompt("Path to service account JSON key file")
            path = os.path.abspath(os.path.expanduser(path))
            if os.path.isfile(path):
                break
            print(f"  No file found at {path}")
        credentials_path = path
    else:
        auth_mode = "adc"
        credentials_path = None

    max_bytes = prompt_positive_int(
        "Max bytes a single BigQuery query may scan (queries over this are refused)",
        default=DEFAULT_MAX_BYTES_BILLED,
    )

    gcp_env = GCPEnvironment(
        name=name, project_id=project_id, region=region,
        auth_mode=auth_mode, credentials_path=credentials_path,
        max_bytes_billed=max_bytes,
    )

    print("Verifying...")
    verify_gcp(gcp_env)

    cfg.gcp_environments[name] = gcp_env
    if not cfg.gcp_default:
        cfg.gcp_default = name
    print(f"Added GCP environment '{name}'.")


# ─── AWS flow ───────────────────────────────────────────────────────────────

def verify_aws(aws_env: AWSEnvironment) -> bool:
    try:
        from providers.aws.auth import verify_credentials
    except ImportError:
        print("  (skipping verification, boto3 not installed yet; run pip install -r requirements.txt)")
        return True
    try:
        identity = verify_credentials(aws_env)
        print(f"  OK, Account: {identity['account']}, ARN: {identity['arn']}")
        return True
    except Exception as e:
        print(f"  FAILED to verify credentials: {e}")
        return False


def add_aws_environment(cfg: AppConfig):
    print("\n--- Add AWS environment ---")
    name = prompt_new_name("Environment name (e.g. 'staging', 'prod')", cfg.aws_environments)

    region = prompt("Region (e.g. us-east-1)")
    auth_choice = prompt_choice("Auth method:", [
        "Existing AWS profile / SSO / role already on this machine",
        "Access key + secret access key",
    ])

    access_key_id = secret_access_key = session_token = None
    if auth_choice.startswith("Existing"):
        auth_mode = "profile"
        profile = prompt("AWS profile name", default="default")
    else:
        auth_mode = "keys"
        profile = None
        print("  Warning: raw keys will be written to .env in plaintext.")
        print("  .env is git-ignored and will be set to permissions 600, but a profile is safer if you have one.")
        access_key_id = prompt("Access Key ID")
        secret_access_key = ""
        while not secret_access_key:
            secret_access_key = getpass.getpass("Secret Access Key (hidden): ").strip()
            if not secret_access_key:
                print("  The secret access key is required.")
        session_token = input("Session Token (optional, press enter to skip): ").strip() or None

    aws_env = AWSEnvironment(
        name=name, region=region, auth_mode=auth_mode, profile=profile,
        access_key_id=access_key_id, secret_access_key=secret_access_key,
        session_token=session_token,
    )

    print("Verifying...")
    ok = verify_aws(aws_env)
    if not ok and not prompt_yes_no("Save this environment anyway?", default_yes=False):
        print("Discarded.")
        return

    if prompt_yes_no("Will you use Athena for SQL queries in this environment?", default_yes=False):
        aws_env.athena_output_location = input(
            "  Athena results S3 location (e.g. s3://my-bucket/athena/) [blank = workgroup default]: "
        ).strip() or None
        aws_env.athena_workgroup = input(
            "  Athena workgroup (set one with a per-query data-scanned cutoff to cap cost) [blank = 'primary']: "
        ).strip() or None

    cfg.aws_environments[name] = aws_env
    if not cfg.aws_default:
        cfg.aws_default = name
    print(f"Added AWS environment '{name}'.")


# ─── main menu ──────────────────────────────────────────────────────────────

def print_status(cfg: AppConfig):
    gcp_count = len(cfg.gcp_environments)
    aws_count = len(cfg.aws_environments)
    print(f"\nLoaded {gcp_count} GCP environment(s): {', '.join(cfg.gcp_environments) or '(none)'}"
          + (f" (default: {cfg.gcp_default})" if cfg.gcp_default else ""))
    print(f"Loaded {aws_count} AWS environment(s): {', '.join(cfg.aws_environments) or '(none)'}"
          + (f" (default: {cfg.aws_default})" if cfg.aws_default else ""))


def remove_environment(cfg: AppConfig):
    all_names = [f"gcp:{n}" for n in cfg.gcp_environments] + [f"aws:{n}" for n in cfg.aws_environments]
    if not all_names:
        print("Nothing to remove.")
        return
    choice = prompt_choice("Remove which environment?", all_names)
    provider, name = choice.split(":", 1)
    if provider == "gcp":
        del cfg.gcp_environments[name]
        if cfg.gcp_default == name:
            cfg.gcp_default = next(iter(cfg.gcp_environments), None)
    else:
        del cfg.aws_environments[name]
        if cfg.aws_default == name:
            cfg.aws_default = next(iter(cfg.aws_environments), None)
    print(f"Removed '{name}'.")


def set_default_environment(cfg: AppConfig):
    provider = prompt_choice("Set default for which provider?", ["gcp", "aws"])
    names = list(cfg.gcp_environments if provider == "gcp" else cfg.aws_environments)
    if not names:
        print(f"No {provider.upper()} environments configured yet.")
        return
    choice = prompt_choice("Default environment:", names)
    if provider == "gcp":
        cfg.gcp_default = choice
    else:
        cfg.aws_default = choice
    print(f"Default {provider.upper()} environment set to '{choice}'.")


def main():
    print("=== Multi-Cloud MCP Setup Wizard ===")
    try:
        cfg = load_config(ENV_FILE) if os.path.isfile(ENV_FILE) else AppConfig()
    except ConfigError as e:
        print(f"\nYour existing {ENV_FILE} is invalid: {e}")
        print("Fix or delete it, then re-run the wizard.")
        sys.exit(1)
    print(f"Config file: {ENV_FILE}")

    print_status(cfg)

    while True:
        choice = prompt_choice("\nMain menu:", [
            "Add a cloud environment",
            "Remove an environment",
            "Set default environment for a provider",
            "Save and exit",
            "Exit without saving",
        ])

        if choice == "Add a cloud environment":
            provider = prompt_choice("Provider?", ["gcp", "aws"])
            if provider == "gcp":
                add_gcp_environment(cfg)
            else:
                add_aws_environment(cfg)
        elif choice == "Remove an environment":
            remove_environment(cfg)
        elif choice == "Set default environment for a provider":
            set_default_environment(cfg)
        elif choice == "Save and exit":
            save_config(cfg, ENV_FILE)
            print(f"\nWrote {ENV_FILE} ({len(cfg.gcp_environments)} GCP, {len(cfg.aws_environments)} AWS environment(s)).")
            print("\nNext steps:")
            print("  pip install -r requirements.txt")
            print("  python server.py")
            break
        elif choice == "Exit without saving":
            print("Discarded changes.")
            break


if __name__ == "__main__":
    try:
        main()
    except (KeyboardInterrupt, EOFError):
        print("\nAborted.")
        sys.exit(1)
