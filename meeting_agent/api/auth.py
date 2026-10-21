import secrets

from fastapi import Header, HTTPException, status

from ..config import get_settings


def require_api_key(x_api_key: str = Header(default="")) -> None:
    if not secrets.compare_digest(x_api_key, get_settings().api_key):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Missing or wrong X-API-Key")


def require_internal_token(x_internal_token: str = Header(default="")) -> None:
    if not secrets.compare_digest(x_internal_token, get_settings().internal_token):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Missing or wrong X-Internal-Token")
