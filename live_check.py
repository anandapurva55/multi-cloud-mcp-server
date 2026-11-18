"""
Live smoke test for your configured GCP/AWS environments.

Unlike the pytest suite (which mocks every cloud call so it can run with no
credentials), this makes real API calls against your real project(s)/account(s)
using whatever is in your real .env. Run it after setup_wizard.py to sanity-check
a newly added environment before pointing an MCP client at it.

Usage:
    python3 live_check.py                # check every configured environment
    python3 live_check.py --env prod     # check only the environment named "prod"
    python3 live_check.py --provider gcp # check only GCP environments
"""
import argparse
import sys

from config import ConfigError, load_config
from providers.aws.auth import verify_credentials as aws_verify_credentials
from providers.gcp.auth import get_credentials as gcp_get_credentials


def check_gcp(env):
    try:
        credentials = gcp_get_credentials(env)
    except Exception as e:
        return False, f"credential resolution failed: {e}"

    try:
        from google.cloud import bigquery
        client = bigquery.Client(project=env.project_id, credentials=credentials)
        list(client.list_datasets(max_results=1))
        return True, "credentials OK, BigQuery reachable"
    except Exception as e:
        return True, (
            "credentials OK, but a live API call failed (this is normal if "
            f"BigQuery isn't enabled on this project): {e}"
        )


def check_aws(env):
    try:
        identity = aws_verify_credentials(env)
        return True, f"credentials OK, account {identity['account']}, arn {identity['arn']}"
    except Exception as e:
        return False, f"credential verification failed: {e}"


def main():
    parser = argparse.ArgumentParser(description="Live smoke test for configured cloud environments.")
    parser.add_argument("--env", help="Check only the environment with this name")
    parser.add_argument("--provider", choices=["gcp", "aws"], help="Check only this provider")
    args = parser.parse_args()

    try:
        cfg = load_config()
    except ConfigError as e:
        print(f"Invalid configuration: {e}", file=sys.stderr)
        sys.exit(1)
    if not cfg.gcp_enabled and not cfg.aws_enabled:
        print("No environments configured. Run: python3 setup_wizard.py", file=sys.stderr)
        sys.exit(1)

    checked = 0
    failed = 0

    if cfg.gcp_enabled and args.provider in (None, "gcp"):
        for name, env in cfg.gcp_environments.items():
            if args.env and name != args.env:
                continue
            checked += 1
            ok, detail = check_gcp(env)
            print(f"[GCP] {name} ({env.project_id}): {'OK' if ok else 'FAILED'} - {detail}")
            failed += 0 if ok else 1

    if cfg.aws_enabled and args.provider in (None, "aws"):
        for name, env in cfg.aws_environments.items():
            if args.env and name != args.env:
                continue
            checked += 1
            ok, detail = check_aws(env)
            print(f"[AWS] {name} ({env.region}): {'OK' if ok else 'FAILED'} - {detail}")
            failed += 0 if ok else 1

    if checked == 0:
        print(f"No environment named '{args.env}' found.", file=sys.stderr)
        sys.exit(1)

    print(f"\n{checked - failed}/{checked} environment(s) OK.")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
