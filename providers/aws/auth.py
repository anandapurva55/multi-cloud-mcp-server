"""AWS session/credential resolution: a named profile (or default chain) or
explicit access keys, depending on the environment's auth_mode."""
import boto3

from config import AWSEnvironment


def get_session(aws_env: AWSEnvironment) -> boto3.Session:
    if aws_env.auth_mode == "keys":
        if not (aws_env.access_key_id and aws_env.secret_access_key):
            # An empty-key Session silently falls back to the default credential chain,
            # which may be a different account than this environment intends.
            raise ValueError(
                f"AWS environment '{aws_env.name}' is set to auth_mode=keys but has no access keys configured."
            )
        return boto3.Session(
            aws_access_key_id=aws_env.access_key_id,
            aws_secret_access_key=aws_env.secret_access_key,
            aws_session_token=aws_env.session_token or None,
            region_name=aws_env.region,
        )
    return boto3.Session(profile_name=aws_env.profile, region_name=aws_env.region)


def get_client(aws_env: AWSEnvironment, service_name: str):
    return get_session(aws_env).client(service_name, region_name=aws_env.region)


def verify_credentials(aws_env: AWSEnvironment) -> dict:
    sts = get_client(aws_env, "sts")
    identity = sts.get_caller_identity()
    return {"account": identity["Account"], "arn": identity["Arn"], "userId": identity["UserId"]}
