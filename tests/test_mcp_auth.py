import asyncio
from urllib.parse import parse_qs, urlparse

from mcp.server.auth.provider import AuthorizationParams
from mcp.shared.auth import OAuthClientInformationFull

from adintel.mcp.auth import (
    GoogleIdentity,
    GoogleOAuthProvider,
    build_google_oauth_provider_from_env,
)


def test_google_oauth_provider_builds_google_authorization_url() -> None:
    provider = GoogleOAuthProvider(
        base_url="https://adintel-mcp.3.15.29.33.sslip.io",
        google_client_id="google-client-id",
        google_client_secret="google-client-secret",
        allowed_domain="feedmob.com",
    )
    client = OAuthClientInformationFull(
        client_id="mcp-client",
        redirect_uris=["https://client.example/callback"],
        token_endpoint_auth_method="none",
        scope="mcp",
    )
    params = AuthorizationParams(
        state="client-state",
        scopes=["mcp"],
        code_challenge="challenge",
        redirect_uri="https://client.example/callback",
        redirect_uri_provided_explicitly=True,
    )

    url = asyncio.run(provider.authorize(client, params))
    parsed = urlparse(url)
    query = parse_qs(parsed.query)

    assert parsed.netloc == "accounts.google.com"
    assert query["client_id"] == ["google-client-id"]
    assert query["redirect_uri"] == ["https://adintel-mcp.3.15.29.33.sslip.io/auth/google/callback"]
    assert query["scope"] == ["openid email"]
    assert query["hd"] == ["feedmob.com"]


def test_google_oauth_provider_restricts_hosted_domain_and_email() -> None:
    provider = GoogleOAuthProvider(
        base_url="https://example.com",
        google_client_id="google-client-id",
        google_client_secret="google-client-secret",
        allowed_domain="feedmob.com",
    )

    assert provider._identity_allowed(GoogleIdentity(email="person@feedmob.com", hd="feedmob.com"))
    assert not provider._identity_allowed(GoogleIdentity(email="person@gmail.com", hd=None))
    assert not provider._identity_allowed(
        GoogleIdentity(email="person@feedmob.com", hd="gmail.com")
    )


def test_google_oauth_provider_env_factory_requires_complete_config(monkeypatch) -> None:
    monkeypatch.delenv("BASE_URL", raising=False)
    monkeypatch.delenv("GOOGLE_CLIENT_ID", raising=False)
    monkeypatch.delenv("GOOGLE_CLIENT_SECRET", raising=False)
    assert build_google_oauth_provider_from_env() is None

    monkeypatch.setenv("BASE_URL", "https://example.com")
    monkeypatch.setenv("GOOGLE_CLIENT_ID", "google-client-id")
    monkeypatch.setenv("GOOGLE_CLIENT_SECRET", "google-client-secret")
    monkeypatch.setenv("ALLOWED_DOMAIN", "feedmob.com")
    assert build_google_oauth_provider_from_env() is not None
