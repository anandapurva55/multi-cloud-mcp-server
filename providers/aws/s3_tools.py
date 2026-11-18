"""S3 tools (bonus coverage): list buckets, list objects under a prefix."""
import json

from config import CONFIG
from providers.aws.auth import get_client
from providers.aws.paging import list_note, paginate
from providers.tools import cloud_tool


def register(mcp):

    @cloud_tool(mcp)
    def s3_list_buckets(env: str = None) -> str:
        """List all S3 buckets. env: a configured AWS environment name (default: the configured default)."""
        aws_env = CONFIG.resolve_aws(env)
        s3 = get_client(aws_env, "s3")
        buckets, truncated = paginate(s3, "list_buckets", "Buckets")
        if not buckets:
            return "No S3 buckets found."
        return "\n".join(f"  • {b['Name']}  (created {b['CreationDate']})" for b in buckets) + list_note(len(buckets), truncated)

    @cloud_tool(mcp)
    def s3_list_objects(bucket: str, prefix: str = "", limit: int = 50, env: str = None) -> str:
        """
        List objects in an S3 bucket under an optional prefix.

        Args:
            bucket: e.g. 'my-bucket'
            prefix: e.g. 'logs/2026/' (default: list from bucket root)
            limit:  Number of objects to return (default 50, max 200).
            env:    a configured AWS environment name (default: the configured default)
        """
        limit = max(1, min(limit, 200))
        aws_env = CONFIG.resolve_aws(env)
        s3 = get_client(aws_env, "s3")
        objects, truncated = paginate(s3, "list_objects_v2", "Contents", limit=limit,
                                      Bucket=bucket, Prefix=prefix)
        if not objects:
            return f"No objects found in '{bucket}/{prefix}'."
        rows = [{"key": o["Key"], "size": o["Size"], "lastModified": str(o["LastModified"])} for o in objects]
        return json.dumps(rows, indent=2, default=str) + list_note(len(rows), truncated)
