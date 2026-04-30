from __future__ import annotations

import os
import secrets
import time
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlencode

import httpx
from mcp.server.auth.provider import (
    AccessToken,
    AuthorizationCode,
    AuthorizationParams,
    OAuthAuthorizationServerProvider,
    RefreshToken,
    construct_redirect_uri,
)
from mcp.shared.auth import OAuthClientInformationFull, OAuthToken
from starlette.requests import Request
from starlette.responses import JSONResponse, RedirectResponse, Response


GOOGLE_AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"
GOOGLE_TOKENINFO_URL = "https://oauth2.googleapis.com/tokeninfo"


@dataclass(frozen=True)
class PendingGoogleAuthorization:
    client_id: str
    params: AuthorizationParams
    expires_at: float


@dataclass(frozen=True)
class GoogleIdentity:
    email: str
    hd: str | None


class GoogleOAuthProvider(
    OAuthAuthorizationServerProvider[AuthorizationCode, RefreshToken, AccessToken]
):
    """OAuth provider that delegates user login to Google Workspace."""

    def __init__(
        self,
        *,
        base_url: str,
        google_client_id: str,
        google_client_secret: str,
        allowed_domain: str = "feedmob.com",
        token_ttl_seconds: int = 3600,
        refresh_token_ttl_seconds: int = 30 * 24 * 3600,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.google_client_id = google_client_id
        self.google_client_secret = google_client_secret
        self.allowed_domain = allowed_domain.lower().strip()
        self.token_ttl_seconds = token_ttl_seconds
        self.refresh_token_ttl_seconds = refresh_token_ttl_seconds

        self._clients: dict[str, OAuthClientInformationFull] = {}
        self._pending_google: dict[str, PendingGoogleAuthorization] = {}
        self._authorization_codes: dict[str, AuthorizationCode] = {}
        self._access_tokens: dict[str, AccessToken] = {}
        self._refresh_tokens: dict[str, RefreshToken] = {}

    @property
    def google_callback_url(self) -> str:
        return f"{self.base_url}/auth/google/callback"

    async def get_client(self, client_id: str) -> OAuthClientInformationFull | None:
        return self._clients.get(client_id)

    async def register_client(self, client_info: OAuthClientInformationFull) -> None:
        if not client_info.client_id:
            raise ValueError("client_id is required")
        self._clients[client_info.client_id] = client_info

    async def authorize(
        self, client: OAuthClientInformationFull, params: AuthorizationParams
    ) -> str:
        if not client.client_id:
            raise ValueError("client_id is required")

        state = secrets.token_urlsafe(32)
        self._pending_google[state] = PendingGoogleAuthorization(
            client_id=client.client_id,
            params=params,
            expires_at=time.time() + 10 * 60,
        )

        query = {
            "client_id": self.google_client_id,
            "redirect_uri": self.google_callback_url,
            "response_type": "code",
            "scope": "openid email",
            "state": state,
            "hd": self.allowed_domain,
            "prompt": "select_account",
        }
        return f"{GOOGLE_AUTH_URL}?{urlencode(query)}"

    async def handle_google_callback(self, request: Request) -> Response:
        error = request.query_params.get("error")
        state = request.query_params.get("state")
        code = request.query_params.get("code")

        if error:
            return JSONResponse({"error": error}, status_code=400)
        if not state or not code:
            return JSONResponse({"error": "missing_state_or_code"}, status_code=400)

        pending = self._pending_google.pop(state, None)
        if pending is None or pending.expires_at < time.time():
            return JSONResponse({"error": "invalid_or_expired_state"}, status_code=400)

        identity = await self._exchange_google_code(code)
        if not self._identity_allowed(identity):
            return JSONResponse(
                {
                    "error": "forbidden_domain",
                    "message": f"Only {self.allowed_domain} Google accounts can access this MCP server.",
                },
                status_code=403,
            )

        auth_code_value = secrets.token_urlsafe(32)
        auth_code = AuthorizationCode(
            code=auth_code_value,
            scopes=pending.params.scopes or [],
            expires_at=time.time() + 5 * 60,
            client_id=pending.client_id,
            code_challenge=pending.params.code_challenge,
            redirect_uri=pending.params.redirect_uri,
            redirect_uri_provided_explicitly=pending.params.redirect_uri_provided_explicitly,
            resource=pending.params.resource,
        )
        self._authorization_codes[auth_code_value] = auth_code

        return RedirectResponse(
            construct_redirect_uri(
                str(pending.params.redirect_uri),
                code=auth_code_value,
                state=pending.params.state,
            ),
            status_code=302,
            headers={"Cache-Control": "no-store"},
        )

    async def _exchange_google_code(self, code: str) -> GoogleIdentity:
        async with httpx.AsyncClient(timeout=15) as client:
            token_response = await client.post(
                GOOGLE_TOKEN_URL,
                data={
                    "code": code,
                    "client_id": self.google_client_id,
                    "client_secret": self.google_client_secret,
                    "redirect_uri": self.google_callback_url,
                    "grant_type": "authorization_code",
                },
            )
            token_response.raise_for_status()
            token_payload: dict[str, Any] = token_response.json()
            id_token = token_payload.get("id_token")
            if not id_token:
                raise ValueError("Google did not return an id_token")

            info_response = await client.get(GOOGLE_TOKENINFO_URL, params={"id_token": id_token})
            info_response.raise_for_status()
            info: dict[str, Any] = info_response.json()

        if info.get("aud") != self.google_client_id:
            raise ValueError("Google id_token audience does not match this OAuth client")
        if info.get("email_verified") not in (True, "true", "True"):
            raise ValueError("Google account email is not verified")

        email = str(info.get("email") or "").lower()
        if not email:
            raise ValueError("Google account email is missing")
        hd = str(info["hd"]).lower() if info.get("hd") else None
        return GoogleIdentity(email=email, hd=hd)

    def _identity_allowed(self, identity: GoogleIdentity) -> bool:
        return identity.hd == self.allowed_domain and identity.email.endswith(
            f"@{self.allowed_domain}"
        )

    async def load_authorization_code(
        self, client: OAuthClientInformationFull, authorization_code: str
    ) -> AuthorizationCode | None:
        code = self._authorization_codes.get(authorization_code)
        if code and code.client_id == client.client_id:
            return code
        return None

    async def exchange_authorization_code(
        self, client: OAuthClientInformationFull, authorization_code: AuthorizationCode
    ) -> OAuthToken:
        self._authorization_codes.pop(authorization_code.code, None)
        access_token = self._new_access_token(
            client.client_id or "", authorization_code.scopes, authorization_code.resource
        )
        refresh_token = self._new_refresh_token(client.client_id or "", authorization_code.scopes)
        return OAuthToken(
            access_token=access_token.token,
            expires_in=self.token_ttl_seconds,
            scope=" ".join(access_token.scopes),
            refresh_token=refresh_token.token,
        )

    async def load_refresh_token(
        self, client: OAuthClientInformationFull, refresh_token: str
    ) -> RefreshToken | None:
        token = self._refresh_tokens.get(refresh_token)
        if token and token.client_id == client.client_id and self._is_active(token.expires_at):
            return token
        return None

    async def exchange_refresh_token(
        self,
        client: OAuthClientInformationFull,
        refresh_token: RefreshToken,
        scopes: list[str],
    ) -> OAuthToken:
        self._refresh_tokens.pop(refresh_token.token, None)
        access_token = self._new_access_token(client.client_id or "", scopes)
        new_refresh_token = self._new_refresh_token(client.client_id or "", scopes)
        return OAuthToken(
            access_token=access_token.token,
            expires_in=self.token_ttl_seconds,
            scope=" ".join(access_token.scopes),
            refresh_token=new_refresh_token.token,
        )

    async def load_access_token(self, token: str) -> AccessToken | None:
        access_token = self._access_tokens.get(token)
        if access_token and self._is_active(access_token.expires_at):
            return access_token
        return None

    async def verify_token(self, token: str) -> AccessToken | None:
        return await self.load_access_token(token)

    async def revoke_token(self, token: AccessToken | RefreshToken) -> None:
        if isinstance(token, AccessToken):
            self._access_tokens.pop(token.token, None)
            return
        self._refresh_tokens.pop(token.token, None)

    def _new_access_token(
        self, client_id: str, scopes: list[str], resource: str | None = None
    ) -> AccessToken:
        token = AccessToken(
            token=secrets.token_urlsafe(32),
            client_id=client_id,
            scopes=scopes,
            expires_at=int(time.time()) + self.token_ttl_seconds,
            resource=resource,
        )
        self._access_tokens[token.token] = token
        return token

    def _new_refresh_token(self, client_id: str, scopes: list[str]) -> RefreshToken:
        token = RefreshToken(
            token=secrets.token_urlsafe(32),
            client_id=client_id,
            scopes=scopes,
            expires_at=int(time.time()) + self.refresh_token_ttl_seconds,
        )
        self._refresh_tokens[token.token] = token
        return token

    @staticmethod
    def _is_active(expires_at: int | None) -> bool:
        return expires_at is None or expires_at > int(time.time())


def build_google_oauth_provider_from_env() -> GoogleOAuthProvider | None:
    google_client_id = os.getenv("GOOGLE_CLIENT_ID")
    google_client_secret = os.getenv("GOOGLE_CLIENT_SECRET")
    base_url = os.getenv("BASE_URL")
    if not (google_client_id and google_client_secret and base_url):
        return None

    return GoogleOAuthProvider(
        base_url=base_url,
        google_client_id=google_client_id,
        google_client_secret=google_client_secret,
        allowed_domain=os.getenv("ALLOWED_DOMAIN", "feedmob.com"),
        token_ttl_seconds=int(os.getenv("OAUTH_ACCESS_TOKEN_TTL_SECONDS", "3600")),
        refresh_token_ttl_seconds=int(
            os.getenv("OAUTH_REFRESH_TOKEN_TTL_SECONDS", str(30 * 24 * 3600))
        ),
    )
