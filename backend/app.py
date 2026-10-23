import asyncio
import hmac
import json
import logging
from contextlib import asynccontextmanager, suppress
from datetime import datetime, timedelta

from fastapi import Depends, FastAPI, HTTPException, Query, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from sqlalchemy import func, select, text
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from starlette.concurrency import run_in_threadpool
from starlette.requests import Request as StarletteRequest

from backend.chat import ChatAgent
from backend.config import Settings
from backend.credentials import CredentialStore
from backend.db import Database
from backend.gateway import Gateway
from backend.models import (
    ApiKey,
    Audit,
    BootstrapState,
    Invite,
    LoginSession,
    Upstream,
    User,
    now,
    uid,
)
from backend.network import validate_header, validate_url
from backend.observability import AuditWindow, audit_summary
from backend.schemas import (
    AcceptInvite,
    ChatBody,
    ConfirmBody,
    LoginBody,
    MemberUpdate,
    NewInvite,
    NewKey,
    PermissionUpdate,
    ServerBody,
    ServerUpdate,
)
from backend.security import (
    Principal,
    RateLimiter,
    authenticate,
    check_password,
    csrf,
    digest,
    hash_password,
    random_token,
    refresh_principal,
)

COOKIE = "harbor_session"
logger = logging.getLogger(__name__)


def iso(value):
    return value.isoformat() + "Z" if isinstance(value, datetime) else value


def public_user(user):
    return {
        key: iso(getattr(user, key))
        for key in ("id", "email", "name", "role", "created_at", "active")
    }


def public_key(key):
    return {
        field: iso(getattr(key, field))
        for field in ("id", "name", "prefix", "user_id", "created_at", "last_used_at", "revoked_at")
    }


def public_server(server):
    return {
        "id": server.id,
        "name": server.name,
        "slug": server.slug,
        "url": server.url,
        "enabled": server.enabled,
        "status": server.status if server.enabled else "Disabled",
        "auth_type": server.auth_type,
        "header_name": server.header_name,
        "has_credential": bool(server.credential_key),
        "last_error": server.last_error,
        "last_checked_at": iso(server.last_checked_at),
        "tools": [
            {
                "name": tool["name"],
                "description": tool.get("description", ""),
                "inputSchema": tool["inputSchema"],
                "permission": server.permissions.get(tool["name"], "write"),
            }
            for tool in server.tools
        ],
    }


class BoundaryMiddleware:
    def __init__(self, app, db, settings, limiter):
        self.app, self.db, self.settings, self.limiter = app, db, settings, limiter

    def record_rejected_call(self, bearer, body):
        if body.get("method") != "tools/call":
            return
        with self.db.session() as session:
            key = session.scalar(select(ApiKey).where(ApiKey.token_hash == digest(bearer)))
            user = session.get(User, key.user_id) if key else None
            if user:
                session.add(
                    Audit(
                        user_id=user.id,
                        user_name=user.name,
                        user_email=user.email,
                        server_name="Gateway",
                        tool_name=str(body.get("params", {}).get("name", "unknown"))[:150],
                        request_id=str(body.get("id", uid()))[:100],
                        outcome="denied",
                        error="Gateway authentication or rate limit rejected the call",
                    )
                )

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        request = StarletteRequest(scope, receive)
        origin = request.headers.get("origin")
        if origin and origin.rstrip("/") not in self.settings.origins:
            return await JSONResponse({"detail": "Origin is not allowed"}, 403)(
                scope, receive, send
            )
        content_length = request.headers.get("content-length", "0")
        try:
            if int(content_length) > 256 * 1024:
                return await JSONResponse({"detail": "Request is too large"}, 413)(
                    scope, receive, send
                )
        except ValueError:
            return await JSONResponse({"detail": "Invalid Content-Length"}, 400)(
                scope, receive, send
            )
        if scope["method"] in {"POST", "PUT", "PATCH", "DELETE"}:
            # Check actual bytes before parsing, including chunked bodies without Content-Length.
            body = bytearray()
            original_receive = receive
            try:
                async with asyncio.timeout(15):
                    while True:
                        message = await original_receive()
                        if message["type"] == "http.disconnect":
                            return
                        body.extend(message.get("body", b""))
                        if len(body) > 256 * 1024:
                            return await JSONResponse({"detail": "Request is too large"}, 413)(
                                scope, original_receive, send
                            )
                        if not message.get("more_body", False):
                            break
            except TimeoutError:
                return await JSONResponse({"detail": "Request body timed out"}, 408)(
                    scope, original_receive, send
                )
            delivered = False

            async def receive():
                nonlocal delivered
                if not delivered:
                    delivered = True
                    return {"type": "http.request", "body": bytes(body), "more_body": False}
                return await original_receive()

            request = StarletteRequest(scope, receive)
        if scope["path"].rstrip("/") == "/mcp":
            authorization = request.headers.get("authorization", "")
            bearer = authorization[7:] if authorization.lower().startswith("bearer ") else None
            try:
                # A slow database connect must not stop the event loop, including
                # health probes and requests which already have database capacity.
                principal = await run_in_threadpool(
                    authenticate, self.db, self.settings, bearer=bearer
                )
                self.limiter.check("mcp:" + principal.id, 120)
                scope["harbor.principal"] = principal
            except HTTPException as exc:
                # Revoked keys can still be attributed, but they never authorize execution.
                if bearer and not bearer.startswith("chat.") and request.method == "POST":
                    try:
                        body = await request.json()
                        await run_in_threadpool(self.record_rejected_call, bearer, body)
                    except Exception:
                        logger.warning("Unable to record rejected MCP request")
                return await JSONResponse(
                    {"detail": exc.detail}, exc.status_code, headers=exc.headers
                )(
                    scope, receive, send
                )
            except SQLAlchemyError:
                logger.warning("Database unavailable during MCP authentication")
                return await JSONResponse(
                    {"detail": "Database is temporarily unavailable. Please retry shortly."},
                    503,
                    headers={"Retry-After": "1"},
                )(scope, receive, send)

        async def secure_send(message):
            if message["type"] == "http.response.start":
                headers = list(message.get("headers", []))
                headers.extend(
                    [
                        (b"x-content-type-options", b"nosniff"),
                        (b"cache-control", b"no-store"),
                        (b"referrer-policy", b"no-referrer"),
                    ]
                )
                message["headers"] = headers
            await send(message)

        await self.app(scope, receive, secure_send)


def create_app(settings: Settings | None = None):
    settings = settings or Settings()
    db = Database(settings.database_url)
    credentials = CredentialStore(settings)
    gateway = Gateway(db, settings, credentials)
    agent = ChatAgent(settings, db, gateway)
    limiter = RateLimiter()

    @asynccontextmanager
    async def lifespan(application):
        if len(settings.session_secret) < 32:
            raise RuntimeError("SESSION_SECRET must contain at least 32 random characters")
        db.migrate(settings.testing)
        with db.session() as session:
            existing_installation = session.scalar(select(User).limit(1)) is not None
            if settings.bootstrap_admin_email and not existing_installation:
                if len(settings.bootstrap_admin_password) < 12:
                    raise RuntimeError("BOOTSTRAP_ADMIN_PASSWORD must be at least 12 characters")
                session.add(
                    User(
                        email=settings.bootstrap_admin_email.strip().lower(),
                        name=settings.bootstrap_admin_name,
                        role="admin",
                        password_hash=hash_password(settings.bootstrap_admin_password),
                    )
                )
            if not session.get(BootstrapState, "defaults"):
                # Defaults are installation seeds. Deleting a registry entry must
                # survive restarts, including an intentionally empty registry.
                if not existing_installation:
                    for server in json.loads(settings.bootstrap_servers_json):
                        if session.scalar(select(Upstream).where(Upstream.slug == server["slug"])):
                            continue
                        validate_url(server["url"], settings.internal_hosts)
                        session.add(
                            Upstream(
                                name=server["name"],
                                slug=server["slug"],
                                url=server["url"],
                                auth_type="bearer" if server.get("credential_key") else "none",
                                credential_key=server.get("credential_key"),
                                permissions={name: "read" for name in server.get("read_tools", [])},
                            )
                        )
                session.add(BootstrapState(name="defaults"))
        async with gateway.server.session_manager.run():
            task = asyncio.create_task(gateway.supervise()) if not settings.testing else None
            try:
                yield
            finally:
                if task:
                    task.cancel()
                    with suppress(asyncio.CancelledError):
                        await task
                await gateway.schema_checks.close()
        db.engine.dispose()

    app = FastAPI(
        title="Harbor API",
        version="0.1.0",
        lifespan=lifespan,
        docs_url="/api/docs",
        openapi_url="/api/openapi.json",
        redoc_url=None,
    )
    app.state.db, app.state.gateway, app.state.settings = db, gateway, settings
    app.state.credentials, app.state.agent = credentials, agent
    app.add_middleware(BoundaryMiddleware, db=db, settings=settings, limiter=limiter)

    @app.exception_handler(IntegrityError)
    async def integrity_error(request, exc):
        return JSONResponse({"detail": "That record already exists or is no longer available"}, 409)

    @app.exception_handler(SQLAlchemyError)
    async def database_error(request, exc):
        logger.warning("Database unavailable during API request")
        return JSONResponse(
            {"detail": "Database is temporarily unavailable. Please retry shortly."},
            503,
            headers={"Retry-After": "1"},
        )

    @app.exception_handler(RequestValidationError)
    async def validation_error(request, exc):
        error = exc.errors()[0]
        field = ".".join(str(part) for part in error["loc"] if part not in {"body", "query"})
        return JSONResponse({"detail": f"{field}: {error['msg']}" if field else error["msg"]}, 422)

    def current(request: Request) -> Principal:
        principal = authenticate(db, settings, cookie=request.cookies.get(COOKIE))
        if request.method not in {"GET", "HEAD", "OPTIONS"}:
            expected = csrf(settings.session_secret, principal.session_id)
            if not hmac.compare_digest(
                request.headers.get("x-csrf-token", "").encode(), expected.encode()
            ):
                raise HTTPException(403, "CSRF token is missing or invalid")
        return principal

    def admin(principal=Depends(current)):
        if principal.role != "admin":
            raise HTTPException(403, "Administrator access is required")
        return principal

    def require_server(session, server_id):
        server = session.get(Upstream, server_id)
        if not server:
            raise HTTPException(404, "Server not found")
        return server

    async def discard_unregistered_credential(key):
        # The remote write may have committed before a disconnect or cancellation.
        # Finish its compensating delete before releasing the registry mutation lock.
        worker = asyncio.create_task(credentials.put(key, None))
        cancelled = False
        while not worker.done():
            try:
                await asyncio.shield(worker)
            except asyncio.CancelledError:
                cancelled = True
            except Exception:
                break
        worker.result()
        if cancelled:
            raise asyncio.CancelledError

    def validate_server(url, auth_type, header_name):
        try:
            validate_url(url, settings.internal_hosts)
            if auth_type == "header":
                validate_header(header_name)
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from None

    def start_session(session, user, request, response):
        # Account changes replace this browser's authority in the same transaction.
        # Other devices stay signed in, and a failed transaction preserves the old session.
        previous_token = request.cookies.get(COOKIE)
        if previous_token:
            previous = session.scalar(
                select(LoginSession).where(LoginSession.token_hash == digest(previous_token))
            )
            if previous and not previous.revoked_at:
                previous.revoked_at = now()
        token = random_token()
        login_session = LoginSession(
            token_hash=digest(token),
            user_id=user.id,
            expires_at=now() + timedelta(hours=settings.session_hours),
        )
        session.add(login_session)
        session.flush()
        response.set_cookie(
            COOKIE,
            token,
            httponly=True,
            samesite="lax",
            secure=settings.secure_cookies or settings.gateway_url.startswith("https://"),
            max_age=settings.session_hours * 3600,
            path="/",
        )
        return {
            "user": public_user(user),
            "csrf_token": csrf(settings.session_secret, login_session.id),
        }

    @app.get("/health/live")
    async def live():
        return {"status": "ok"}

    @app.get("/health/ready")
    def ready():
        try:
            with db.session() as session:
                session.execute(text("SELECT 1"))
            return {"status": "ok"}
        except Exception:
            raise HTTPException(503, "Database is unavailable") from None

    @app.get("/api/health")
    def health():
        return {
            "status": "ok",
            "chat_configured": bool(settings.gemini_api_key),
            "store_url": settings.store_url,
            "gateway_url": settings.gateway_url,
        }

    @app.post("/api/auth/login")
    def login(body: LoginBody, request: Request, response: Response):
        limiter.check("login:" + (request.client.host if request.client else "unknown"), 10)
        with db.session() as session:
            user = session.scalar(select(User).where(User.email == body.email))
            if not user or not user.active or not check_password(user.password_hash, body.password):
                raise HTTPException(401, "Invalid email or password")
            return start_session(session, user, request, response)

    @app.get("/api/auth/me")
    def me(principal=Depends(current)):
        with db.session() as session:
            user = session.get(User, principal.id)
            return {
                "user": public_user(user),
                "csrf_token": csrf(settings.session_secret, principal.session_id),
            }

    @app.post("/api/auth/logout")
    def logout(response: Response, principal=Depends(current)):
        with db.session() as session:
            session.get(LoginSession, principal.session_id).revoked_at = now()
        response.delete_cookie(COOKIE, path="/")
        return {"status": "signed_out"}

    @app.get("/api/invites/{token}")
    def invite_preview(token: str, request: Request):
        limiter.check("invite:" + (request.client.host if request.client else "unknown"), 30)
        with db.session() as session:
            invite = session.scalar(select(Invite).where(Invite.token_hash == digest(token)))
            if not invite or invite.accepted_at or invite.expires_at <= now():
                raise HTTPException(404, "Invitation is invalid, expired, or already used")
            return {
                "email": invite.email,
                "role": invite.role,
                "expires_at": iso(invite.expires_at),
            }

    @app.post("/api/auth/accept-invite")
    def accept_invite(body: AcceptInvite, request: Request, response: Response):
        limiter.check("accept:" + (request.client.host if request.client else "unknown"), 10)
        with db.session() as session:
            invite = session.scalar(
                select(Invite).where(Invite.token_hash == digest(body.token)).with_for_update()
            )
            if not invite or invite.accepted_at or invite.expires_at <= now():
                raise HTTPException(404, "Invitation is invalid, expired, or already used")
            user = User(
                email=invite.email,
                name=body.name.strip(),
                role=invite.role,
                password_hash=hash_password(body.password),
            )
            session.add(user)
            invite.accepted_at = now()
            session.flush()
            return start_session(session, user, request, response)

    @app.get("/api/members")
    def members(principal=Depends(admin)):
        with db.session() as session:
            return [
                public_user(user)
                for user in session.scalars(select(User).order_by(User.created_at))
            ]

    @app.patch("/api/members/{user_id}")
    def edit_member(user_id: str, body: MemberUpdate, principal=Depends(admin)):
        with db.session() as session:
            # Serializing administrator edits also prevents removing the last active administrator.
            admins = list(
                session.scalars(
                    select(User)
                    .where(User.role == "admin", User.active.is_(True))
                    .order_by(User.id)
                    .with_for_update()
                )
            )
            # This row lock may have waited behind another administrator's edit.
            # Recheck the caller after that edit, before changing any account.
            admin(refresh_principal(db, principal))
            user = session.get(User, user_id)
            if not user:
                raise HTTPException(404, "Member not found")
            if (
                user.role == "admin"
                and user.active
                and len(admins) == 1
                and (body.role == "member" or body.active is False)
            ):
                raise HTTPException(409, "Keep at least one active administrator")
            if body.role is not None:
                user.role = body.role
            if body.active is not None:
                user.active = body.active
            return public_user(user)

    @app.get("/api/invites")
    def invites(principal=Depends(admin)):
        with db.session() as session:
            return [
                {
                    field: iso(getattr(invite, field))
                    for field in ("id", "email", "role", "expires_at", "accepted_at")
                }
                for invite in session.scalars(select(Invite).order_by(Invite.expires_at.desc()))
            ]

    @app.post("/api/invites", status_code=201)
    def create_invite(body: NewInvite, principal=Depends(admin)):
        token = random_token()
        with db.session() as session:
            if session.scalar(select(User).where(User.email == body.email)):
                raise HTTPException(409, "A member with that email already exists")
            invite = Invite(
                email=body.email,
                role=body.role,
                token_hash=digest(token),
                expires_at=now() + timedelta(days=2),
            )
            session.add(invite)
            session.flush()
            origin = settings.gateway_url.rsplit("/mcp", 1)[0]
            return {
                "id": invite.id,
                "email": invite.email,
                "role": invite.role,
                "expires_at": iso(invite.expires_at),
                "accepted_at": None,
                "invite_url": f"{origin}/invite/{token}",
            }

    @app.get("/api/keys")
    def keys(principal=Depends(current)):
        with db.session() as session:
            statement = select(ApiKey).order_by(ApiKey.created_at.desc())
            if principal.role != "admin":
                statement = statement.where(ApiKey.user_id == principal.id)
            return [public_key(key) for key in session.scalars(statement)]

    @app.post("/api/keys", status_code=201)
    def create_key(body: NewKey, principal=Depends(current)):
        user_id = body.user_id or principal.id
        if user_id != principal.id and principal.role != "admin":
            raise HTTPException(403, "You can only create your own keys")
        with db.session() as session:
            user = session.get(User, user_id)
            if not user or not user.active:
                raise HTTPException(404, "Active member not found")
            token = "harbor_" + random_token()
            key = ApiKey(
                user_id=user_id, name=body.name.strip(), prefix=token[:12], token_hash=digest(token)
            )
            session.add(key)
            session.flush()
            return {"key": token, "record": public_key(key)}

    @app.delete("/api/keys/{key_id}")
    def revoke_key(key_id: str, principal=Depends(current)):
        with db.session() as session:
            key = session.get(ApiKey, key_id)
            if not key or (key.user_id != principal.id and principal.role != "admin"):
                raise HTTPException(404, "Key not found")
            if not key.revoked_at:
                key.revoked_at = now()
            return public_key(key)

    @app.get("/api/servers")
    def servers(principal=Depends(current)):
        with db.session() as session:
            return [
                public_server(server)
                for server in session.scalars(select(Upstream).order_by(Upstream.slug))
            ]

    @app.post("/api/servers", status_code=201)
    async def add_server(body: ServerBody, principal=Depends(admin)):
        async with gateway.registry_lock:
            admin(refresh_principal(db, principal))
            validate_server(body.url, body.auth_type, body.header_name)
            if body.auth_type != "none" and not body.credential:
                raise HTTPException(422, "A credential is required for authenticated servers")
            if body.credential and ("\r" in body.credential or "\n" in body.credential):
                raise HTTPException(422, "Credential must fit in one HTTP header")
            server_id = uid()
            credential_key = f"upstream-{server_id}" if body.credential else None
            database_attempted = False
            try:
                if credential_key:
                    try:
                        await credentials.put(credential_key, body.credential)
                    except Exception:
                        raise HTTPException(
                            503, "Cannot update the upstream credential Secret"
                        ) from None
                database_attempted = True
                with db.session() as session:
                    server = Upstream(
                        id=server_id,
                        name=body.name,
                        slug=body.slug,
                        url=body.url,
                        auth_type=body.auth_type,
                        header_name=body.header_name,
                        credential_key=credential_key,
                        enabled=body.enabled,
                    )
                    session.add(server)
                    session.flush()
            except (Exception, asyncio.CancelledError):
                if database_attempted:
                    # A failed commit response does not prove the commit failed.
                    # Never remove a credential that a saved connection may use.
                    try:
                        with db.session() as session:
                            saved = session.get(Upstream, server_id) is not None
                    except SQLAlchemyError:
                        logger.warning("Connection save status is unknown; credential retained")
                        raise HTTPException(
                            503, "Connection save could not be confirmed. Refresh connections before retrying."
                        ) from None
                    if saved:
                        raise HTTPException(
                            503, "Connection was saved, but its response failed. Refresh connections before retrying."
                        ) from None
                if credential_key:
                    try:
                        await discard_unregistered_credential(credential_key)
                    except Exception:
                        logger.warning("Unused upstream credential cleanup was not confirmed")
                        raise HTTPException(
                            503, "Connection was not saved; credential cleanup could not be confirmed."
                        ) from None
                raise
        await gateway.refresh(server_id)
        with db.session() as session:
            return public_server(require_server(session, server_id))

    @app.patch("/api/servers/{server_id}")
    async def edit_server(server_id: str, body: ServerUpdate, principal=Depends(admin)):
        async with gateway.registry_lock:
            admin(refresh_principal(db, principal))
            with db.session() as session:
                server = require_server(session, server_id)
            values = body.model_dump(exclude_unset=True)
            effective_url = values.get("url") or server.url
            validate_server(
                effective_url,
                values.get("auth_type") or server.auth_type,
                values.get("header_name", server.header_name),
            )
            credential_key = server.credential_key
            if body.credential and ("\r" in body.credential or "\n" in body.credential):
                raise HTTPException(422, "Credential must fit in one HTTP header")
            credential_change = bool(body.credential or (body.clear_credential and credential_key))
            if credential_change:
                # A remote Secret write can succeed even when its response is lost.
                # Fail closed until both stores agree, including request cancellation.
                with db.session() as session:
                    changing = require_server(session, server_id)
                    changing.enabled, changing.status = False, "Disabled"
                    changing.revision += 1
                    changing.last_error = "Credential update is incomplete. Re-enter the credential and enable the connection."
            try:
                if body.clear_credential and credential_key:
                    await credentials.put(credential_key, None)
                    credential_key = None
                elif body.credential:
                    credential_key = credential_key or f"upstream-{server_id}"
                    await credentials.put(credential_key, body.credential)
            except Exception:
                raise HTTPException(
                    503,
                    "Credential update was not confirmed. Connection remains disabled; re-enter the credential and enable it.",
                ) from None
            with db.session() as session:
                current_server = require_server(session, server_id)
                for field in ("name", "url", "auth_type", "header_name", "enabled"):
                    if field in values and (values[field] is not None or field == "header_name"):
                        setattr(current_server, field, values[field])
                if credential_change and values.get("enabled") is None:
                    current_server.enabled = server.enabled
                current_server.credential_key = credential_key
                current_server.revision += 1
                if effective_url != server.url:
                    # An endpoint change is a new trust decision. Do not inherit read permissions.
                    current_server.tools, current_server.permissions = [], {}
                if not current_server.enabled:
                    current_server.status, current_server.last_error = "Disabled", None
        await gateway.refresh(server_id)
        with db.session() as session:
            return public_server(require_server(session, server_id))

    @app.delete("/api/servers/{server_id}")
    async def remove_server(server_id: str, principal=Depends(admin)):
        async with gateway.registry_lock:
            admin(refresh_principal(db, principal))
            with db.session() as session:
                server = require_server(session, server_id)
                server.enabled, server.status = False, "Disabled"
                server.revision += 1
                credential_key = server.credential_key
            if credential_key:
                try:
                    await credentials.put(credential_key, None)
                except Exception:
                    raise HTTPException(
                        503, "Server disabled, but credential deletion failed. Retry deletion."
                    ) from None
            with db.session() as session:
                session.delete(require_server(session, server_id))
            return {"status": "removed"}

    @app.post("/api/servers/{server_id}/refresh")
    async def refresh_server(server_id: str, principal=Depends(admin)):
        with db.session() as session:
            require_server(session, server_id)
        await gateway.refresh(server_id)
        with db.session() as session:
            return public_server(require_server(session, server_id))

    @app.patch("/api/servers/{server_id}/tools/permissions")
    async def tool_permissions(server_id: str, body: PermissionUpdate, principal=Depends(admin)):
        async with gateway.registry_lock:
            admin(refresh_principal(db, principal))
            with db.session() as session:
                server = require_server(session, server_id)
                names = {tool["name"] for tool in server.tools}
                if not set(body.permissions).issubset(names):
                    raise HTTPException(
                        422, "Permissions can only name tools in the current catalog"
                    )
                server.permissions = {**server.permissions, **body.permissions}
                return public_server(server)

    @app.get("/api/audit/summary")
    def activity_summary(
        window: AuditWindow = "24h",
        server_id: str | None = Query(None, max_length=36),
        user_id: str | None = Query(None, max_length=36),
        principal=Depends(current),
    ):
        try:
            return audit_summary(db, principal, window, now(), server_id, user_id)
        except SQLAlchemyError:
            logger.warning("Unable to load activity summary")
            raise HTTPException(
                503, "Activity summary is temporarily unavailable", headers={"Retry-After": "1"}
            ) from None

    @app.get("/api/audit")
    def audit(
        limit: int = Query(50, ge=1, le=200),
        offset: int = Query(0, ge=0),
        outcome: str | None = None,
        server_id: str | None = None,
        user_id: str | None = None,
        principal=Depends(current),
    ):
        conditions = []
        if principal.role != "admin":
            conditions.append(Audit.user_id == principal.id)
        elif user_id:
            conditions.append(Audit.user_id == user_id)
        if outcome:
            conditions.append(Audit.outcome == outcome)
        if server_id:
            conditions.append(Audit.server_id == server_id)
        with db.session() as session:
            total = session.scalar(select(func.count()).select_from(Audit).where(*conditions))
            rows = session.scalars(
                select(Audit)
                .where(*conditions)
                .order_by(Audit.timestamp.desc())
                .limit(limit)
                .offset(offset)
            )
            return {
                "total": total,
                "items": [
                    {
                        column.name: iso(getattr(row, column.name))
                        for column in Audit.__table__.columns
                    }
                    for row in rows
                ],
            }

    @app.post("/api/chat")
    async def chat(body: ChatBody, principal=Depends(current)):
        limiter.check("chat:" + principal.id, 12)
        return await agent.run(principal, body.messages)

    @app.post("/api/chat/confirm")
    async def confirm(body: ConfirmBody, principal=Depends(current)):
        return await agent.confirm(principal, body.id, body.approve)

    # REST routes are matched first. The MCP child supplies the exact /mcp ASGI route.
    app.mount("/", gateway.mcp_app)
    return app


app = create_app()
