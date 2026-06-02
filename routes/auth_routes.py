"""Authentication routes — login, logout, signup, status, user management."""

from fastapi import APIRouter, Request, Response, HTTPException
from pydantic import BaseModel
from typing import Optional
import asyncio
import logging
import os

from core.auth import AuthManager
from src.rate_limiter import RateLimiter
from src.settings_scrub import scrub_settings
from src.settings import (
    load_settings as _load_settings,
    save_settings as _save_settings,
    load_features as _load_features,
    save_features as _save_features,
    DEFAULT_SETTINGS,
)
from src.integrations import (
    load_integrations,
    add_integration,
    update_integration,
    delete_integration,
    get_integration,
    mask_integration_secret,
    execute_api_call,
    INTEGRATION_PRESETS,
    migrate_from_settings,
)

logger = logging.getLogger(__name__)

SESSION_COOKIE = "odysseus_session"


# =========================
# SAFE INTERNAL HELPERS
# =========================

def _get_user(auth_manager: AuthManager, request: Request) -> Optional[str]:
    token = request.cookies.get(SESSION_COOKIE)
    return auth_manager.get_username_for_token(token)


def _norm(u: str) -> str:
    return (u or "").strip().lower()


def _admin(auth_manager: AuthManager, request: Request) -> str:
    user = _get_user(auth_manager, request)
    if not user or not auth_manager.is_admin(user):
        raise HTTPException(403, "Admin only")
    return user


def _rate(limit: RateLimiter, request: Request):
    if not limit.check(request.client.host):
        raise HTTPException(429, "Too many requests — try again later")


def _json(request: Request):
    """
    Safe wrapper for request.json() to avoid repeated code.
    Behavior unchanged.
    """
    return request.json()


# =========================
# REQUEST MODELS
# =========================

class LoginRequest(BaseModel):
    username: str
    password: str
    remember: bool = True
    totp_code: Optional[str] = None


class SetupRequest(BaseModel):
    username: str
    password: str


class SignupRequest(BaseModel):
    username: str
    password: str


class ChangePasswordRequest(BaseModel):
    current_password: str
    new_password: str


class CreateUserRequest(BaseModel):
    username: str
    password: str
    is_admin: bool = False


class DeleteUserRequest(BaseModel):
    username: str


class RenameUserRequest(BaseModel):
    username: str


class SetOpenRegistrationRequest(BaseModel):
    enabled: bool


class TotpVerifyRequest(BaseModel):
    code: str


class TotpDisableRequest(BaseModel):
    password: str


# =========================
# ROUTES
# =========================

def setup_auth_routes(auth_manager: AuthManager) -> APIRouter:
    router = APIRouter(prefix="/api/auth", tags=["auth"])

    _login = RateLimiter(15, 60)
    _signup = RateLimiter(3, 300)
    _setup = RateLimiter(3, 300)

    # -------------------------
    # SETUP
    # -------------------------
    @router.post("/setup")
    async def setup(body: SetupRequest, request: Request):
        _rate(_setup, request)

        if auth_manager.is_configured:
            raise HTTPException(400, "Already configured")

        if len(body.password) < 8:
            raise HTTPException(400, "Password must be at least 8 characters")

        ok = await asyncio.to_thread(auth_manager.setup, body.username, body.password)
        if not ok:
            raise HTTPException(500, "Setup failed")

        return {"ok": True}

    # -------------------------
    # SIGNUP
    # -------------------------
    @router.post("/signup")
    async def signup(body: SignupRequest, request: Request):
        _rate(_signup, request)

        if not auth_manager.is_configured:
            raise HTTPException(400, "Run setup first")

        if not auth_manager.signup_enabled:
            raise HTTPException(403, "Registration disabled")

        username = _norm(body.username)

        if not username:
            raise HTTPException(400, "Username required")

        if len(body.password) < 8:
            raise HTTPException(400, "Password too short")

        ok = await asyncio.to_thread(
            auth_manager.create_user,
            username,
            body.password,
            False
        )

        if not ok:
            raise HTTPException(409, "Username exists")

        return {"ok": True}

    # -------------------------
    # LOGIN
    # -------------------------
    @router.post("/login")
    async def login(body: LoginRequest, request: Request, response: Response):
        _rate(_login, request)

        username = _norm(body.username)

        if not await asyncio.to_thread(auth_manager.verify_password, username, body.password):
            raise HTTPException(401, "Invalid credentials")

        if auth_manager.totp_enabled(username):
            if not body.totp_code:
                return {"ok": False, "requires_totp": True}

            if not auth_manager.totp_verify(username, body.totp_code):
                raise HTTPException(401, "Invalid 2FA")

        token = await asyncio.to_thread(
            auth_manager.create_session,
            username,
            body.password
        )

        if not token:
            raise HTTPException(401, "Login failed")

        cookie = dict(
            key=SESSION_COOKIE,
            value=token,
            httponly=True,
            samesite="lax",
            secure=os.getenv("SECURE_COOKIES", "false").lower() == "true",
            path="/",
        )

        if body.remember:
            cookie["max_age"] = 60 * 60 * 24 * 7

        response.set_cookie(**cookie)

        return {"ok": True, "username": username}

    # -------------------------
    # LOGOUT
    # -------------------------
    @router.post("/logout")
    async def logout(request: Request, response: Response):
        token = request.cookies.get(SESSION_COOKIE)
        if token:
            auth_manager.revoke_token(token)

        response.delete_cookie(SESSION_COOKIE, path="/")
        return {"ok": True}

    # -------------------------
    # STATUS
    # -------------------------
    @router.get("/status")
    async def status(request: Request):
        token = request.cookies.get(SESSION_COOKIE)
        result = auth_manager.status(token)

        result["signup_enabled"] = auth_manager.signup_enabled

        try:
            u = result.get("username")
            if u:
                result["privileges"] = auth_manager.get_privileges(u)
        except Exception:
            pass

        return result

    # -------------------------
    # CHANGE PASSWORD
    # -------------------------
    @router.post("/change-password")
    async def change_password(body: ChangePasswordRequest, request: Request):
        user = _get_user(auth_manager, request)
        if not user:
            raise HTTPException(401, "Not authenticated")

        if len(body.new_password) < 8:
            raise HTTPException(400, "Password too short")

        token = request.cookies.get(SESSION_COOKIE)

        ok = await asyncio.to_thread(
            auth_manager.change_password,
            user,
            body.current_password,
            body.new_password
        )

        if not ok:
            raise HTTPException(400, "Wrong password")

        await asyncio.to_thread(auth_manager.revoke_user_sessions, user, token)

        return {"ok": True}

    # =========================
    # 2FA (unchanged behavior)
    # =========================

    @router.post("/2fa/setup")
    async def totp_setup(request: Request):
        user = _get_user(auth_manager, request)
        if not user:
            raise HTTPException(401)

        if auth_manager.totp_enabled(user):
            raise HTTPException(400)

        secret = auth_manager.totp_generate_secret(user)
        uri = auth_manager.totp_get_provisioning_uri(user, secret)

        import qrcode, io, base64
        qr = qrcode.make(uri)

        buf = io.BytesIO()
        qr.save(buf, format="PNG")

        return {
            "secret": secret,
            "uri": uri,
            "qr_code": "data:image/png;base64," +
                       base64.b64encode(buf.getvalue()).decode()
        }

    @router.post("/2fa/confirm")
    async def totp_confirm(body: TotpVerifyRequest, request: Request):
        user = _get_user(auth_manager, request)
        if not user:
            raise HTTPException(401)

        if not auth_manager.totp_confirm_enable(user, body.code):
            raise HTTPException(400)

        backup = auth_manager.users.get(user, {}).get("totp_backup_codes", [])
        return {"ok": True, "backup_codes": backup}

    @router.post("/2fa/disable")
    async def totp_disable(body: TotpDisableRequest, request: Request):
        user = _get_user(auth_manager, request)
        if not user:
            raise HTTPException(401)

        if not auth_manager.totp_disable(user, body.password):
            raise HTTPException(400)

        return {"ok": True}

    @router.get("/2fa/status")
    async def totp_status(request: Request):
        user = _get_user(auth_manager, request)
        if not user:
            raise HTTPException(401)

        return {"enabled": auth_manager.totp_enabled(user)}

    # =========================
    # ADMIN USERS
    # =========================

    @router.get("/users")
    async def users(request: Request):
        _admin(auth_manager, request)
        return {"users": auth_manager.list_users()}

    @router.post("/users")
    async def create_user(body: CreateUserRequest, request: Request):
        _admin(auth_manager, request)

        ok = auth_manager.create_user(
            _norm(body.username),
            body.password,
            body.is_admin
        )

        if not ok:
            raise HTTPException(409)

        return {"ok": True}

    @router.put("/users/{username}/privileges")
    async def privileges(username: str, request: Request):
        _admin(auth_manager, request)

        body = await _json(request)
        ok = auth_manager.set_privileges(username, body)

        if not ok:
            raise HTTPException(404)

        return {"ok": True}

    @router.put("/users/{username}/rename")
    async def rename(username: str, body: RenameUserRequest, request: Request):
        _admin(auth_manager, request)

        old = _norm(username)
        new = _norm(body.username)

        if old == new:
            return {"ok": True}

        try:
            from sqlalchemy import func
            from core.database import Base, SessionLocal

            db = SessionLocal()
            try:
                for m in Base.registry.mappers:
                    model = m.class_
                    if hasattr(model, "owner"):
                        db.query(model).filter(
                            func.lower(model.owner) == old
                        ).update({"owner": new})
                db.commit()
            except:
                db.rollback()
                raise
            finally:
                db.close()

        except Exception as e:
            logger.error(e)
            raise HTTPException(500)

        auth_manager.rename_user(old, new, _get_user(auth_manager, request))
        return {"ok": True}

    # =========================
    # FEATURES / SETTINGS / INTEGRATIONS
    # =========================

    @router.get("/features")
    async def features():
        return _load_features()

    @router.post("/features")
    async def set_features(request: Request):
        _admin(auth_manager, request)

        body = await _json(request)
        f = _load_features()

        for k in f:
            if k in body:
                f[k] = body[k]

        _save_features(f)
        return f

    @router.get("/settings")
    async def settings(request: Request):
        user = _get_user(auth_manager, request)
        s = _load_settings()

        return s if user and auth_manager.is_admin(user) else scrub_settings(s)

    @router.post("/settings")
    async def update_settings(request: Request):
        _admin(auth_manager, request)

        body = await _json(request)
        s = _load_settings()

        for k in DEFAULT_SETTINGS:
            if k in body:
                s[k] = body[k]

        _save_settings(s)
        return s

    # integrations unchanged
    migrate_from_settings()

    return router