from datetime import date, datetime
from decimal import Decimal
from functools import wraps
from typing import Any, Callable, TypeVar, cast

import jwt
from flask import Blueprint, current_app, g, jsonify, request

from database import school
from database.db import get_user_profile
from extensions import limiter


api_bp = Blueprint("api", __name__, url_prefix="/api/v1")
F = TypeVar("F", bound=Callable[..., Any])


@api_bp.after_request
def prevent_api_caching(response):
    response.headers["Cache-Control"] = "no-store"
    response.headers["X-Content-Type-Options"] = "nosniff"
    return response


def jwt_required(required_scope: str) -> Callable[[F], F]:
    def decorate(view: F) -> F:
        @wraps(view)
        def wrapped(*args: Any, **kwargs: Any) -> Any:
            scheme, separator, token = request.headers.get(
                "Authorization", ""
            ).partition(" ")
            if scheme.lower() != "bearer" or not separator or not token:
                return _unauthorized()

            try:
                claims = jwt.decode(
                    token,
                    current_app.config["JWT_SECRET"],
                    algorithms=["HS256"],
                    audience="greenfield-api",
                    issuer="greenfield-academy",
                    options={
                        "require": ["sub", "role", "scope", "iat", "exp", "iss", "aud"]
                    },
                )
                user_id = int(claims["sub"])
            except (jwt.InvalidTokenError, TypeError, ValueError):
                return _unauthorized()

            scopes = claims.get("scope")
            if not isinstance(scopes, str) or required_scope not in scopes.split():
                return jsonify({"error": "insufficient_scope"}), 403

            user = get_user_profile(user_id)
            if (
                user is None
                or not user["is_active"]
                or user["role"] != claims.get("role")
                or user.get("auth_version", 0) != claims.get("ver", 0)
            ):
                return _unauthorized()
            g.api_user = user
            g.api_claims = claims
            return view(*args, **kwargs)

        return cast(F, wrapped)

    return decorate


@api_bp.get("/me")
@limiter.limit("60 per minute")
@jwt_required("profile:read")
def me():
    user = g.api_user
    return jsonify(
        {
            "id": user["id"],
            "full_name": user["full_name"],
            "email": user["email"],
            "role": user["role"],
        }
    )


@api_bp.get("/dashboard")
@limiter.limit("60 per minute")
@jwt_required("dashboard:read")
def dashboard():
    raw_term = request.args.get("term")
    if raw_term is None:
        term = school.CURRENT_TERM
    else:
        try:
            term = int(raw_term)
        except ValueError:
            return jsonify({"error": "invalid_term"}), 400
        if term not in (1, 2, 3):
            return jsonify({"error": "invalid_term"}), 400

    user = g.api_user
    if user["role"] == "admin":
        data = school.get_admin_dashboard(term)
    elif user["role"] == "teacher":
        data = school.get_teacher_dashboard(user["id"], term)
    else:
        data = school.get_student_dashboard(user["id"], term)
        if data is None:
            return jsonify({"error": "student_record_unavailable"}), 404
    return jsonify(_json_safe(data))


def _unauthorized():
    response = jsonify({"error": "invalid_or_missing_token"})
    response.status_code = 401
    response.headers["WWW-Authenticate"] = "Bearer"
    response.headers["Cache-Control"] = "no-store"
    return response


def _json_safe(value: Any) -> Any:
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    if isinstance(value, dict):
        return {key: _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(item) for item in value]
    return value
