from fastapi import Header, HTTPException, status

from atlas.config import get_settings


def require_token(authorization: str = Header(default="")) -> None:
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
    if authorization != f"Bearer {token}":
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="invalid or missing bearer token",
        )
