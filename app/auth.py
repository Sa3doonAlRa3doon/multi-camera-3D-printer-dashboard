"""Session authentication and CSRF checks."""

from __future__ import annotations

import secrets
from typing import Any

from fastapi import HTTPException, Request, status


def signed_in(request: Request) -> bool:
    return bool(request.session.get("authenticated"))


def require_auth(request: Request) -> None:
    if not signed_in(request):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Sign in required")


def csrf_token(request: Request) -> str:
    token = request.session.get("csrf")
    if not token:
        token = secrets.token_urlsafe(32)
        request.session["csrf"] = token
    return str(token)


def require_csrf(request: Request) -> None:
    require_auth(request)
    expected = request.session.get("csrf")
    supplied = request.headers.get("x-csrf-token")
    if not expected or not supplied or not secrets.compare_digest(str(expected), supplied):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Invalid CSRF token")
