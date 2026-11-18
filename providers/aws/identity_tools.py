"""Identity tool (bonus coverage): confirm which AWS account a named environment points to."""
from config import CONFIG
from providers.aws.auth import verify_credentials
from providers.tools import cloud_tool


def register(mcp):

    @cloud_tool(mcp)
    def aws_whoami(env: str = None) -> str:
        """
        Show which AWS account/identity a configured environment resolves to.

        Args:
            env: a configured AWS environment name (default: the configured default)
        """
        aws_env = CONFIG.resolve_aws(env)
        identity = verify_credentials(aws_env)
        return (
            f"Environment: {aws_env.name}\n"
            f"Account:     {identity['account']}\n"
            f"ARN:         {identity['arn']}\n"
            f"Region:      {aws_env.region}"
        )
