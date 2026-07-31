import secrets

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from atlas.config import get_settings

# Registering HTTPBearer as the dependency makes /docs render an "Authorize"
# button (paste the token only — Swagger adds the "Bearer " prefix) and marks
# protected endpoints with a lock. auto_error=False so we control the
# responses: 503 when the server has no token configured, 401 otherwise.
_bearer = HTTPBearer(
    auto_error=False,
    description="Paste your ATLAS_API_TOKEN (no 'Bearer ' prefix).",
)


def require_token(
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer),
) -> None:
    """Bearer-token gate for every endpoint except /health.

    The API fronts private data (broker phone numbers, deal notes) from day
    one — an unset token locks the API rather than opening it.
    """
    token = get_settings().atlas_api_token
    if not token:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="ATLAS_API_TOKEN is not configured; API is locked",
        )
    provided = credentials.credentials if credentials else ""
    # Constant-time comparison: the token guards an internet-facing API and
    # must not be recoverable byte-by-byte via response timing.
    if not secrets.compare_digest(provided.encode(), token.encode()):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="invalid or missing bearer token",
        )
