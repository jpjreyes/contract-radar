"""Who is calling: the demo user switcher, or a verified OpenID Connect token (Microsoft Entra ID in production).

CC_AUTH=demo  the `X-User` header picks one of the demo users (the public demo's user switcher).
CC_AUTH=oidc  the request must carry `Authorization: Bearer <JWT>` from the identity provider. The token's signature
              is checked against the provider's published keys (JWKS), plus issuer, audience and expiry. The person
              is then mapped to an app user **inside Postgres** (`resolve_identity`): first by their own id (email /
              username claim), else by their groups (Entra security groups), so "who may see what" is managed as group
              membership in the company directory and enforced by row-level security. The app role can't read or
              edit the mapping table; it can only ask the SECURITY DEFINER function.

Config (oidc): CC_OIDC_ISSUER (e.g. https://login.microsoftonline.com/<tenant>/v2.0), CC_OIDC_AUDIENCE (the app's
client id), optional CC_OIDC_JWKS_URL (default: from the issuer's discovery document), CC_OIDC_USER_CLAIM
(default preferred_username).
"""

from __future__ import annotations

import os
from functools import lru_cache

import jwt
import psycopg

SETUP = """
CREATE TABLE IF NOT EXISTS identity_map (
    kind      text NOT NULL CHECK (kind IN ('user', 'group')),
    value     text NOT NULL,          -- email / username claim, or a directory group id
    app_user  text NOT NULL REFERENCES app_users (name) ON DELETE CASCADE,
    PRIMARY KEY (kind, value)
);
REVOKE ALL ON identity_map FROM {role};

CREATE OR REPLACE FUNCTION resolve_identity(who text, groups text[]) RETURNS text
LANGUAGE sql STABLE SECURITY DEFINER SET search_path = public AS $$
    SELECT coalesce(
        (SELECT app_user FROM identity_map WHERE kind = 'user' AND lower(value) = lower(who)),
        (SELECT app_user FROM identity_map WHERE kind = 'group' AND value = ANY (groups) ORDER BY app_user LIMIT 1))
$$;
REVOKE ALL ON FUNCTION resolve_identity(text, text[]) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION resolve_identity(text, text[]) TO {role};
"""


class AuthError(Exception):
    pass


def setup(admin: psycopg.Connection, role: str) -> None:
    admin.execute(SETUP.replace("{role}", role))


def mode() -> str:
    return os.environ.get("CC_AUTH", "demo")


@lru_cache(maxsize=4)
def _jwks_client(url: str) -> jwt.PyJWKClient:
    return jwt.PyJWKClient(url, cache_keys=True)


def _jwks_url(issuer: str) -> str:
    explicit = os.environ.get("CC_OIDC_JWKS_URL")
    if explicit:
        return explicit
    import httpx
    return httpx.get(issuer.rstrip("/") + "/.well-known/openid-configuration", timeout=5).json()["jwks_uri"]


def signing_key(token: str):
    """The provider's public key for this token (separate function so tests can swap in a local key)."""
    issuer = os.environ["CC_OIDC_ISSUER"]
    return _jwks_client(_jwks_url(issuer)).get_signing_key_from_jwt(token).key


def verify(token: str) -> dict:
    issuer, audience = os.environ.get("CC_OIDC_ISSUER"), os.environ.get("CC_OIDC_AUDIENCE")
    if not issuer or not audience:
        raise AuthError("OIDC is not configured")
    try:
        return jwt.decode(token, signing_key(token), algorithms=["RS256"], audience=audience, issuer=issuer,
                          options={"require": ["exp", "iat", "iss", "aud"]})
    except jwt.PyJWTError as e:
        raise AuthError(f"invalid token: {e.__class__.__name__}") from e


def resolve(conn: psycopg.Connection, claims: dict) -> str:
    who = str(claims.get(os.environ.get("CC_OIDC_USER_CLAIM", "preferred_username"), ""))
    groups = [str(g) for g in claims.get("groups", [])]
    app_user = conn.execute("SELECT resolve_identity(%s, %s)", (who, groups)).fetchone()[0]
    if not app_user:
        raise AuthError("signed in, but not mapped to any role in this app")
    return app_user
