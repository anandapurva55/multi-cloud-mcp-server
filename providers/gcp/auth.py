"""GCP credential resolution: Application Default Credentials or a
service-account JSON key file, depending on the environment's auth_mode."""
import google.auth
import google.auth.transport.requests
from google.oauth2 import service_account

from config import GCPEnvironment

SCOPES = ["https://www.googleapis.com/auth/cloud-platform"]


def get_credentials(gcp_env: GCPEnvironment):
    if gcp_env.auth_mode == "key_file":
        if not gcp_env.credentials_path:
            raise ValueError(
                f"GCP environment '{gcp_env.name}' is set to auth_mode=key_file "
                f"but has no credentials_path configured."
            )
        credentials = service_account.Credentials.from_service_account_file(
            gcp_env.credentials_path, scopes=SCOPES
        )
    else:
        credentials, _ = google.auth.default(scopes=SCOPES)

    if not credentials.valid:
        credentials.refresh(google.auth.transport.requests.Request())
    return credentials


def get_access_token(gcp_env: GCPEnvironment) -> str:
    credentials = get_credentials(gcp_env)
    credentials.refresh(google.auth.transport.requests.Request())
    return credentials.token
