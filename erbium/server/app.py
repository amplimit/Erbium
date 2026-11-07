from base64 import b64decode
from binascii import Error as Base64Error
from dataclasses import asdict, dataclass
from hmac import compare_digest
from logging import getLogger
from operator import itemgetter
from os import environ
from pathlib import Path
from hashlib import sha256
from re import Match, fullmatch, sub
from ipaddress import ip_address
from struct import error as StructError, unpack
from subprocess import CalledProcessError, TimeoutExpired, run
from threading import BoundedSemaphore
from time import time
from typing import Annotated, Any, Awaitable, Callable, Literal

from fastapi import APIRouter, Depends, FastAPI, HTTPException, Request, Response
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, StringConstraints, field_validator

from erbium.api import Job, Node, NotificationKind, QuotaError, Settings, User, get_all_gpu_info
from erbium.server.auth import EMAIL_PATTERN, LoginThrottle, MAX_PASSWORD_LENGTH, MIN_PASSWORD_LENGTH, \
    USERNAME_PATTERN, hash_password, new_token, token_digest, verify_password
from erbium.server.availability import availability_payload

logger = getLogger(__name__)

ASSETS_DIR: Path = Path(__file__).with_name("assets")
SESSION_COOKIE: str = "__Host-erbium_session"
SESSION_SECONDS: int = 7 * 24 * 3600
SAFE_METHODS: frozenset[str] = frozenset({"GET", "HEAD", "OPTIONS"})
PAGE_POLICY: str = (
    "default-src 'self'; script-src 'self'; style-src 'self'; connect-src 'self'; "
    "img-src 'self' data:; frame-ancestors 'none'; base-uri 'none'; form-action 'self'"
)
KEY_TYPES: frozenset[str] = frozenset({
    "ssh-ed25519", "ssh-rsa", "ecdsa-sha2-nistp256", "ecdsa-sha2-nistp384", "ecdsa-sha2-nistp521",
    "sk-ssh-ed25519@openssh.com", "sk-ecdsa-sha2-nistp256@openssh.com"
})
MAX_PUBLIC_KEYS: int = 10

Username = Annotated[str, StringConstraints(pattern=USERNAME_PATTERN)]
Password = Annotated[str, StringConstraints(min_length=MIN_PASSWORD_LENGTH, max_length=MAX_PASSWORD_LENGTH)]
Email = Annotated[str, StringConstraints(strip_whitespace=True, max_length=254, pattern=EMAIL_PATTERN)]
JobName = Annotated[str, StringConstraints(strip_whitespace=True, pattern=r"^[A-Za-z0-9][A-Za-z0-9 ._-]{0,63}$")]
Hours = Annotated[float, Field(gt=0, le=10000)]
PackageName = Annotated[str, StringConstraints(strip_whitespace=True, pattern=r"^[a-z0-9](?:[a-z0-9+.-]{0,126}[a-z0-9+.])?$")]


def validate_public_key(line: str) -> str:
    parts = line.strip().split(maxsplit=2)
    if len(parts) < 2 or parts[0] not in KEY_TYPES:
        raise ValueError("Each public key must look like \"ssh-ed25519 AAAA... comment\" without options")
    try:
        blob = b64decode(parts[1], validate=True)
        (length,) = unpack(">I", blob[:4])
        embedded = blob[4:4 + length].decode()
    except (Base64Error, StructError, ValueError, UnicodeDecodeError) as error:
        raise ValueError("A public key is not valid base64") from error
    if embedded != parts[0]:
        raise ValueError("A public key's type does not match its contents")
    comment = parts[2] if len(parts) > 2 else ""
    if not fullmatch(r"[\x20-\x7e]{0,128}", comment):
        raise ValueError("Public key comments must be printable ASCII")
    return " ".join(part for part in (parts[0], parts[1], comment) if part)


class LoginModel(BaseModel):
    username: Annotated[str, StringConstraints(max_length=64)]
    password: Annotated[str, StringConstraints(max_length=MAX_PASSWORD_LENGTH)]


class ProfileModel(BaseModel):
    email: Email | None = None
    notifications: dict[NotificationKind, bool]
    public_keys: list[str] = Field(default_factory=list, max_length=MAX_PUBLIC_KEYS)

    @field_validator("public_keys")
    @classmethod
    def _validate_keys(cls, keys: list[str]) -> list[str]:
        return [validate_public_key(key) for key in keys if key.strip()]


class PasswordChangeModel(BaseModel):
    current_password: Annotated[str, StringConstraints(max_length=MAX_PASSWORD_LENGTH)]
    new_password: Password


class JobModel(BaseModel):
    name: JobName
    requested_run_time_hrs: Hours


class RunTimeModel(BaseModel):
    requested_run_time_hrs: Hours


class MoveModel(BaseModel):
    offset: Literal[-1, 1]


class SettingsModel(BaseModel):
    max_run_time_hrs: float = Field(gt=0, le=10000)
    max_jobs_per_user: int = Field(ge=1, le=100)
    max_queue_length: int = Field(ge=1, le=10000)
    ending_notice_minutes: float = Field(ge=0, le=1440)


class NewUserModel(BaseModel):
    username: Username
    password: Password
    email: Email | None = None
    is_admin: bool = False


class AccountModel(BaseModel):
    email: Email | None = None
    is_admin: bool


class PasswordResetModel(BaseModel):
    password: Password


class AptInstallModel(BaseModel):
    packages: list[PackageName] = Field(min_length=1, max_length=50)


@dataclass(frozen=True)
class Principal(object):
    user: User
    csrf_token: str
    token_digest: str


@dataclass
class Runtime(object):
    node: Node
    throttle: LoginThrottle
    hashing: BoundedSemaphore
    pages: dict[str, str]


def _runtime(request: Request) -> Runtime:
    return request.app.state.runtime


def _principal(request: Request) -> Principal | None:
    token = request.cookies.get(SESSION_COOKIE)
    if not token:
        return None
    digest = token_digest(token)
    session = _runtime(request).node.store.get_session(digest)
    return None if session is None else Principal(session[0], session[1], digest)


def require_user(request: Request) -> Principal:
    principal = _principal(request)
    if principal is None:
        raise HTTPException(401, "Not signed in")
    if request.method not in SAFE_METHODS and not compare_digest(
            request.headers.get("x-csrf-token", ""), principal.csrf_token):
        raise HTTPException(403, "Missing or invalid CSRF token")
    return principal


def require_admin(principal: Annotated[Principal, Depends(require_user)]) -> Principal:
    if not principal.user.is_admin:
        raise HTTPException(403, "Administrator access required")
    return principal


CurrentUser = Annotated[Principal, Depends(require_user)]


def _client_address(request: Request) -> str:
    peer = request.client.host if request.client else "unknown"
    try:
        loopback = ip_address(peer).is_loopback
    except ValueError:
        loopback = False
    forwarded = request.headers.get("cf-connecting-ip")
    return forwarded if forwarded and not loopback else peer


def _set_session_cookie(response: Response, token: str) -> None:
    response.set_cookie(SESSION_COOKIE, token, max_age=SESSION_SECONDS, httponly=True, samesite="lax", secure=True,
                        path="/")


def _clear_session_cookie(response: Response) -> None:
    response.delete_cookie(SESSION_COOKIE, path="/", httponly=True, samesite="lax", secure=True)


def _page(runtime: Runtime, name: str) -> HTMLResponse:
    return HTMLResponse(runtime.pages[name], headers={"Content-Security-Policy": PAGE_POLICY, "Cache-Control": "no-cache"})


def _job_view(job: Job, *, estimated_start: float | None = None) -> dict[str, Any]:
    return {
        "id": job.id, "username": job.username, "name": job.name, "status": job.status,
        "requested_run_time_hrs": job.requested_run_time_hrs, "created_at": job.created_at,
        "started_at": job.started_at, "ends_at": job.ends_at(), "ended_at": job.ended_at,
        "end_reason": job.end_reason, "estimated_start": estimated_start
    }


def _user_view(user: User) -> dict[str, Any]:
    return {
        "username": user.username, "email": user.email, "is_admin": user.is_admin,
        "public_keys": list(user.public_keys), "notifications": user.notifications, "created_at": user.created_at
    }


def _gpus(node: Node) -> list[dict[str, Any]]:
    try:
        payload = availability_payload(node, get_all_gpu_info())
    except Exception:
        logger.warning("GPU telemetry is unavailable", exc_info=True)
        return []
    return sorted(payload.values(), key=itemgetter("device_id"))


def _queue_view(node: Node) -> dict[str, Any]:
    now = time()
    running = node.store.running_job()
    plan = node.schedule(now)
    return {
        "running": None if running is None else _job_view(running),
        "queue": [_job_view(job, estimated_start=start) for job, start in plan],
        "wait_time_hrs": node.wait_time_hrs(now, plan=plan)
    }


def _require(job: Job | None, detail: str) -> Job:
    if job is None:
        raise HTTPException(404, detail)
    return job


def _public_routes() -> APIRouter:
    router = APIRouter()

    @router.get("/")
    def index() -> RedirectResponse:
        return RedirectResponse("/dash", status_code=303)

    @router.get("/login", response_class=HTMLResponse)
    def login_page(request: Request) -> HTMLResponse:
        return _page(_runtime(request), "login")

    @router.get("/dash", response_class=HTMLResponse)
    def dash_page(request: Request) -> Response:
        if _principal(request) is None:
            return RedirectResponse("/login", status_code=303)
        return _page(_runtime(request), "dash")

    @router.get("/admin", response_class=HTMLResponse)
    def admin_page(request: Request) -> Response:
        principal = _principal(request)
        if principal is None:
            return RedirectResponse("/login", status_code=303)
        if not principal.user.is_admin:
            return RedirectResponse("/dash", status_code=303)
        return _page(_runtime(request), "admin")

    @router.post("/api/login")
    def login(credentials: LoginModel, request: Request, response: Response) -> dict[str, Any]:
        runtime = _runtime(request)
        address = _client_address(request)
        pair = f"pair:{address}:{credentials.username}"
        if not runtime.throttle.attempt(f"address:{address}", pair):
            raise HTTPException(429, "Too many sign-in attempts, try again later")
        user = runtime.node.store.get_user(credentials.username)
        with runtime.hashing:
            valid = verify_password(credentials.password, None if user is None else user.password_hash)
        if not valid or user is None:
            raise HTTPException(401, "Invalid username or password")
        runtime.throttle.reset(pair)
        token = new_token()
        runtime.node.store.create_session(token_digest(token), user.username, new_token(), time() + SESSION_SECONDS)
        _set_session_cookie(response, token)
        return {"username": user.username, "is_admin": user.is_admin}

    return router


def _user_routes() -> APIRouter:
    router = APIRouter(prefix="/api", dependencies=[Depends(require_user)])

    @router.post("/logout")
    def logout(principal: CurrentUser, request: Request, response: Response) -> dict[str, bool]:
        _runtime(request).node.store.delete_session(principal.token_digest)
        _clear_session_cookie(response)
        return {"signed_out": True}

    @router.get("/me")
    def me(principal: CurrentUser, request: Request) -> dict[str, Any]:
        settings = _runtime(request).node.store.get_settings()
        return {**_user_view(principal.user), "csrf_token": principal.csrf_token, "limits": asdict(settings)}

    @router.put("/me")
    def update_me(profile: ProfileModel, principal: CurrentUser, request: Request) -> dict[str, Any]:
        node = _runtime(request).node
        node.store.update_profile(principal.user.username, email=profile.email, public_keys=tuple(profile.public_keys),
                                  notifications={**principal.user.notifications, **profile.notifications})
        if tuple(profile.public_keys) != principal.user.public_keys:
            node.public_keys_changed(principal.user.username)
        user = node.store.get_user(principal.user.username)
        assert user is not None
        return _user_view(user)

    @router.post("/me/password")
    def change_password(change: PasswordChangeModel, principal: CurrentUser, request: Request,
                        response: Response) -> dict[str, bool]:
        runtime = _runtime(request)
        with runtime.hashing:
            valid = verify_password(change.current_password, principal.user.password_hash)
            password_hash = hash_password(change.new_password) if valid else ""
        if not valid:
            raise HTTPException(403, "Current password is incorrect")
        runtime.node.store.set_password_hash(principal.user.username, password_hash)
        _clear_session_cookie(response)
        return {"changed": True}

    @router.get("/status")
    def status(request: Request) -> dict[str, Any]:
        node = _runtime(request).node
        return {**_queue_view(node), "gpus": _gpus(node)}

    @router.post("/jobs")
    def join(job: JobModel, principal: CurrentUser, request: Request) -> dict[str, Any]:
        try:
            created = _runtime(request).node.join(principal.user.username, job.name, job.requested_run_time_hrs)
        except QuotaError as error:
            raise HTTPException(409, str(error)) from error
        return _job_view(created)

    @router.delete("/jobs/{job_id}")
    def leave(job_id: int, principal: CurrentUser, request: Request) -> dict[str, Any]:
        job = _runtime(request).node.leave(job_id, principal.user.username)
        return _job_view(_require(job, "No queued job of yours has that id"))

    @router.post("/jobs/{job_id}/stop")
    def stop(job_id: int, principal: CurrentUser, request: Request) -> dict[str, Any]:
        job = _runtime(request).node.stop(job_id, username=principal.user.username)
        return _job_view(_require(job, "No running job of yours has that id"))

    @router.get("/session")
    def session(principal: CurrentUser, request: Request) -> dict[str, Any] | None:
        running = _runtime(request).node.store.running_job()
        if running is None or running.username != principal.user.username:
            return None
        return {"job": _job_view(running), "ssh_password": running.ssh_password, "jupyter_token": running.jupyter_token}

    return router


def _admin_routes() -> APIRouter:
    router = APIRouter(prefix="/api/admin", dependencies=[Depends(require_admin)])

    @router.get("/overview")
    def overview(request: Request) -> dict[str, Any]:
        node = _runtime(request).node
        return {
            **_queue_view(node), "history": [_job_view(job) for job in node.store.history(50)],
            "users": [_user_view(user) for user in node.store.list_users()],
            "settings": asdict(node.store.get_settings())
        }

    @router.post("/jobs/{job_id}/stop")
    def stop(job_id: int, request: Request) -> dict[str, Any]:
        return _job_view(_require(_runtime(request).node.stop(job_id), "No running job has that id"))

    @router.delete("/jobs/{job_id}")
    def remove(job_id: int, request: Request) -> dict[str, Any]:
        return _job_view(_require(_runtime(request).node.remove(job_id), "No queued job has that id"))

    @router.post("/jobs/{job_id}/move")
    def move(job_id: int, move: MoveModel, request: Request) -> dict[str, bool]:
        if not _runtime(request).node.store.move_job(job_id, move.offset):
            raise HTTPException(409, "The job cannot be moved in that direction")
        return {"moved": True}

    @router.put("/jobs/{job_id}/run_time")
    def set_run_time(job_id: int, run_time: RunTimeModel, request: Request) -> dict[str, Any]:
        job = _runtime(request).node.store.set_requested_run_time(job_id, run_time.requested_run_time_hrs)
        return _job_view(_require(job, "No active job has that id"))

    @router.put("/settings")
    def update_settings(settings: SettingsModel, request: Request) -> dict[str, Any]:
        store = _runtime(request).node.store
        store.put_settings(Settings(**settings.model_dump()))
        return asdict(store.get_settings())

    @router.post("/users")
    def create_user(user: NewUserModel, request: Request) -> dict[str, Any]:
        try:
            runtime = _runtime(request)
            with runtime.hashing:
                password_hash = hash_password(user.password)
            created = runtime.node.store.create_user(user.username, password_hash, email=user.email,
                                                     is_admin=user.is_admin)
        except ValueError as error:
            raise HTTPException(409, str(error)) from error
        return _user_view(created)

    @router.patch("/users/{username}")
    def update_user(username: str, account: AccountModel, request: Request) -> dict[str, Any]:
        store = _runtime(request).node.store
        try:
            if not store.update_account(username, email=account.email, is_admin=account.is_admin):
                raise HTTPException(404, "No user has that name")
        except ValueError as error:
            raise HTTPException(409, str(error)) from error
        user = store.get_user(username)
        assert user is not None
        return _user_view(user)

    @router.post("/users/{username}/password")
    def reset_password(username: str, reset: PasswordResetModel, request: Request) -> dict[str, bool]:
        runtime = _runtime(request)
        with runtime.hashing:
            password_hash = hash_password(reset.password)
        if not runtime.node.store.set_password_hash(username, password_hash):
            raise HTTPException(404, "No user has that name")
        return {"changed": True}

    @router.delete("/users/{username}")
    def delete_user(username: str, request: Request) -> dict[str, bool]:
        try:
            if not _runtime(request).node.store.delete_user(username):
                raise HTTPException(404, "No user has that name")
        except ValueError as error:
            raise HTTPException(409, str(error)) from error
        return {"deleted": True}

    @router.post("/apt_install")
    def apt_install(packages: AptInstallModel) -> dict[str, list[str]]:
        try:
            environment = {**environ, "DEBIAN_FRONTEND": "noninteractive"}
            run(("apt-get", "update"), check=True, capture_output=True, timeout=600, env=environment)
            run(("apt-get", "install", "-y", *packages.packages), check=True, capture_output=True, timeout=1800,
                env=environment)
        except (CalledProcessError, TimeoutExpired) as error:
            logger.error("apt-get install failed: %s", getattr(error, "stderr", b"") or error)
            raise HTTPException(500, "apt-get install failed, check the server log") from error
        return {"installed": packages.packages}

    return router


def _asset_version(match: Match[str]) -> str:
    digest = sha256((ASSETS_DIR / match.group(1)).read_bytes()).hexdigest()[:12]
    return f"/assets/{match.group(1)}?v={digest}\""


def _load_page(name: str) -> str:
    return sub(r'/assets/([\w.-]+)"', _asset_version, (ASSETS_DIR / f"{name}.html").read_text(encoding="utf-8"))


def create_app(node: Node) -> FastAPI:
    app = FastAPI(title="Erbium Server", docs_url=None, redoc_url=None, openapi_url=None)
    app.state.runtime = Runtime(node, LoginThrottle(), BoundedSemaphore(2), {
        name: _load_page(name) for name in ("login", "dash", "admin")
    })

    @app.middleware("http")
    async def security_headers(request: Request, call_next: Callable[[Request], Awaitable[Response]]) -> Response:
        response = await call_next(request)
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("X-Frame-Options", "DENY")
        response.headers.setdefault("Referrer-Policy", "no-referrer")
        if request.url.path.startswith("/api"):
            response.headers["Cache-Control"] = "no-store"
        return response

    app.include_router(_public_routes())
    app.include_router(_user_routes())
    app.include_router(_admin_routes())
    app.mount("/assets", StaticFiles(directory=ASSETS_DIR), name="assets")
    return app
