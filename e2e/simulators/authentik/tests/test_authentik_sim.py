"""Authentik-simulator: OIDC (Authorization Code + PKCE, JWKS, userinfo, refresh, introspect, revoke), beheer-API
zoals home-control-hub die gebruikt, en foutinjectie."""

from __future__ import annotations

import base64
import hashlib
import json
import secrets
from collections.abc import Iterator
from typing import Any
from urllib.parse import parse_qs, urlparse

import jwt
import pytest
from authentik_sim import create_app
from starlette.testclient import TestClient

BASE = "http://testserver"


@pytest.fixture
def client() -> Iterator[TestClient]:
    with TestClient(create_app()) as c:
        yield c


def hub(client: TestClient) -> dict[str, str]:
    return dict(client.get("/_sim/sleutels").json()["hub_client"])


def pkce() -> tuple[str, str]:
    verifier = secrets.token_urlsafe(48)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    return verifier, challenge


def authorize(client: TestClient, c: dict[str, str], challenge: str, nonce: str = "n-123",
              redirect: str | None = None, **extra: str) -> Any:
    params = {"response_type": "code", "client_id": c["client_id"], "redirect_uri": redirect or c["redirect_uri"],
              "scope": "openid email profile", "state": "s-456", "nonce": nonce, "code_challenge": challenge,
              "code_challenge_method": "S256", **extra}
    return client.get("/application/o/authorize/", params=params, follow_redirects=False)


def code_flow(client: TestClient) -> tuple[dict[str, Any], dict[str, str]]:
    c = hub(client)
    verifier, challenge = pkce()
    r = authorize(client, c, challenge)
    assert r.status_code == 302, r.text
    loc = urlparse(r.headers["location"])
    q = parse_qs(loc.query)
    assert f"{loc.scheme}://{loc.netloc}{loc.path}" == c["redirect_uri"] and q["state"] == ["s-456"]
    t = client.post("/application/o/token/", data={
        "grant_type": "authorization_code", "code": q["code"][0], "redirect_uri": c["redirect_uri"],
        "code_verifier": verifier}, auth=(c["client_id"], c["client_secret"]))
    assert t.status_code == 200, t.text
    assert t.headers["cache-control"] == "no-store"
    return dict(t.json()), {**c, "code": q["code"][0], "verifier": verifier}


def verify(client: TestClient, id_token: str, client_id: str) -> dict[str, Any]:
    disc = client.get("/application/o/hub/.well-known/openid-configuration").json()
    keys = {k["kid"]: jwt.PyJWK(k) for k in client.get("/application/o/hub/jwks/").json()["keys"]}
    kid = jwt.get_unverified_header(id_token)["kid"]
    return dict(jwt.decode(id_token, keys[kid].key, algorithms=["RS256"], audience=client_id, issuer=disc["issuer"]))


def test_health() -> None:
    with TestClient(create_app()) as c:
        assert c.get("/-/health/live/").status_code == 200 and c.get("/-/health/ready/").status_code == 200
        c.post("/_sim/fouten", json={"naam": "niet_gereed"})
        assert c.get("/-/health/ready/").status_code == 503 and c.get("/-/health/live/").status_code == 200


def test_discovery_matches_authentik(client: TestClient) -> None:
    d = client.get("/application/o/hub/.well-known/openid-configuration").json()
    assert d["issuer"] == f"{BASE}/application/o/hub/"
    assert d["authorization_endpoint"] == f"{BASE}/application/o/authorize/"
    assert d["token_endpoint"] == f"{BASE}/application/o/token/"
    assert d["userinfo_endpoint"] == f"{BASE}/application/o/userinfo/"
    assert d["jwks_uri"] == f"{BASE}/application/o/hub/jwks/"
    assert d["end_session_endpoint"] == f"{BASE}/application/o/hub/end-session/"
    assert d["code_challenge_methods_supported"] == ["plain", "S256"]
    assert d["id_token_signing_alg_values_supported"] == ["RS256"]
    assert set(d["token_endpoint_auth_methods_supported"]) == {"client_secret_post", "client_secret_basic"}
    assert client.get("/application/o/bestaat-niet/.well-known/openid-configuration").status_code == 404


def test_code_pkce_flow_id_token_userinfo_and_groups(client: TestClient) -> None:
    tokens, c = code_flow(client)
    assert tokens["token_type"] == "Bearer" and tokens["expires_in"] == 300 and "refresh_token" in tokens
    claims = verify(client, tokens["id_token"], c["client_id"])
    assert claims["nonce"] == "n-123" and claims["preferred_username"] == "anna" and claims["acr"]
    assert "hub-beheerders" in claims["groups"] and claims["email_verified"] is True
    ui = client.get("/application/o/userinfo/", headers={"Authorization": f"Bearer {tokens['access_token']}"}).json()
    assert ui["sub"] == claims["sub"] and ui["groups"] == claims["groups"]
    # code is eenmalig
    data = {"grant_type": "authorization_code", "code": c["code"], "redirect_uri": c["redirect_uri"],
            "code_verifier": c["verifier"]}
    again = client.post("/application/o/token/", data=data, auth=(c["client_id"], c["client_secret"]))
    assert again.status_code == 400 and again.json()["error"] == "invalid_grant"


def test_pkce_redirect_and_client_checks(client: TestClient) -> None:
    c = hub(client)
    verifier, challenge = pkce()
    # niet-geregistreerde redirect → 400, nooit doorsturen
    r = authorize(client, c, challenge, redirect="https://kwaad.invalid/cb")
    assert r.status_code == 400 and "location" not in r.headers
    r = client.get("/application/o/authorize/", params={"client_id": "onbekend", "redirect_uri": c["redirect_uri"]},
                   follow_redirects=False)
    assert r.status_code == 400
    r = authorize(client, c, challenge, response_type="token")
    assert parse_qs(urlparse(r.headers["location"]).query)["error"] == ["unsupported_response_type"]
    code = parse_qs(urlparse(authorize(client, c, challenge).headers["location"]).query)["code"][0]
    fout = client.post("/application/o/token/", data={"grant_type": "authorization_code", "code": code,
                                                       "redirect_uri": c["redirect_uri"], "code_verifier": "verkeerd"},
                       auth=(c["client_id"], c["client_secret"]))
    assert fout.status_code == 400 and fout.json()["error"] == "invalid_grant"
    geen_client = client.post("/application/o/token/", data={"grant_type": "authorization_code", "code": "x",
                                                              "client_id": c["client_id"], "client_secret": "fout"})
    assert geen_client.status_code == 401 and geen_client.json()["error"] == "invalid_client"
    # niet aangemeld in de browser → naar de inlogflow, geen code
    client.post("/_sim/oidc/ingelogd", json={"gebruiker": None})
    r = authorize(client, c, challenge)
    assert r.status_code == 302 and r.headers["location"].startswith("/if/flow/default-authentication-flow/")
    _ = verifier


def test_refresh_rotation_introspect_revoke_end_session(client: TestClient) -> None:
    tokens, c = code_flow(client)
    auth = (c["client_id"], c["client_secret"])
    r = client.post("/application/o/token/", data={"grant_type": "refresh_token",
                                                    "refresh_token": tokens["refresh_token"]}, auth=auth)
    assert r.status_code == 200 and r.json()["refresh_token"] != tokens["refresh_token"]
    oud = client.post("/application/o/token/", data={"grant_type": "refresh_token",
                                                      "refresh_token": tokens["refresh_token"]}, auth=auth)
    assert oud.status_code == 400 and oud.json()["error"] == "invalid_grant"
    nieuw = r.json()["access_token"]
    assert client.post("/application/o/introspect/", data={"token": nieuw}, auth=auth).json()["active"] is True
    assert client.post("/application/o/revoke/", data={"token": nieuw}, auth=auth).status_code == 200
    assert client.post("/application/o/introspect/", data={"token": nieuw}, auth=auth).json() == {"active": False}
    assert client.get("/application/o/userinfo/", headers={"Authorization": f"Bearer {nieuw}"}).status_code == 401
    r = client.get("/application/o/hub/end-session/", params={"post_logout_redirect_uri": c["redirect_uri"]},
                   follow_redirects=False)
    assert r.status_code == 302 and r.headers["location"] == c["redirect_uri"]
    assert authorize(client, c, pkce()[1]).headers["location"].startswith("/if/flow/")


@pytest.mark.parametrize(
    ("fout", "verwacht"),
    [
        ("issuer_fout", jwt.InvalidIssuerError),
        ("aud_fout", jwt.InvalidAudienceError),
        ("verlopen", jwt.ExpiredSignatureError),
        ("handtekening_fout", jwt.InvalidSignatureError),
        ("kid_onbekend", KeyError),
        ("alg_none", KeyError),
    ],
)
def test_token_faults_are_detected_by_a_correct_client(client: TestClient, fout: str, verwacht: type) -> None:
    client.post("/_sim/fouten", json={"naam": fout})
    tokens, c = code_flow(client)
    with pytest.raises(verwacht):
        verify(client, tokens["id_token"], c["client_id"])


def test_nonce_groups_and_key_rotation_faults(client: TestClient) -> None:
    client.post("/_sim/fouten", json={"naam": "nonce_fout"})
    tokens, c = code_flow(client)
    assert verify(client, tokens["id_token"], c["client_id"])["nonce"] != "n-123"
    client.delete("/_sim/fouten")
    client.post("/_sim/fouten", json={"naam": "groepen_ontbreken"})
    tokens, c = code_flow(client)
    assert "groups" not in verify(client, tokens["id_token"], c["client_id"])
    client.delete("/_sim/fouten")
    oude_jwks = client.get("/application/o/hub/jwks/").json()["keys"]
    client.post("/_sim/fouten", json={"naam": "sleutelrotatie"})
    tokens, c = code_flow(client)
    nieuwe_jwks = client.get("/application/o/hub/jwks/").json()["keys"]
    assert [k["kid"] for k in nieuwe_jwks] != [k["kid"] for k in oude_jwks]
    kid = jwt.get_unverified_header(tokens["id_token"])["kid"]
    assert kid not in {k["kid"] for k in oude_jwks}  # een client met een oude cache moet de JWKS opnieuw ophalen
    assert verify(client, tokens["id_token"], c["client_id"])["preferred_username"] == "anna"


@pytest.mark.parametrize(("fout", "pad", "status"), [
    ("discovery_down", "/application/o/hub/.well-known/openid-configuration", 500),
    ("jwks_down", "/application/o/hub/jwks/", 500),
])
def test_endpoint_faults(client: TestClient, fout: str, pad: str, status: int) -> None:
    client.post("/_sim/fouten", json={"naam": fout})
    assert client.get(pad).status_code == status


def test_token_and_userinfo_faults(client: TestClient) -> None:
    tokens, c = code_flow(client)
    client.post("/_sim/fouten", json={"naam": "userinfo_401"})
    r = client.get("/application/o/userinfo/", headers={"Authorization": f"Bearer {tokens['access_token']}"})
    assert r.status_code == 401
    client.post("/_sim/fouten", json={"naam": "token_500"})
    r = client.post("/application/o/token/", data={"grant_type": "refresh_token",
                                                    "refresh_token": tokens["refresh_token"]},
                    auth=(c["client_id"], c["client_secret"]))
    assert r.status_code == 500


# ------------------------------------------------------------------ beheer-API
def api(client: TestClient, pad: str, token: str | None = None, **params: Any) -> Any:
    s = client.get("/_sim/sleutels").json()
    hdr = {"Authorization": f"Bearer {token or s['api_lees_token']}"}
    return client.get(f"/api/v3{pad}", headers=hdr, params=params)


def test_api_auth_errors_like_authentik(client: TestClient) -> None:
    r = client.get("/api/v3/admin/version/")
    assert r.status_code == 403 and r.json() == {"detail": "Authentication credentials were not provided."}
    r = api(client, "/admin/version/", token="ongeldig")
    assert r.status_code == 403 and r.json() == {"detail": "Token invalid/expired"}
    assert api(client, "/admin/version/").json()["version_current"] == "2026.8.5"
    assert api(client, "/core/users/me/").json()["user"]["username"] == "hch-lezer"


def test_api_lists_pagination_and_filters(client: TestClient) -> None:
    r = api(client, "/core/users/", include_groups="true", page_size=2).json()
    assert r["pagination"]["next"] == 2 and r["pagination"]["count"] == 6 and len(r["results"]) == 2
    assert "groups_obj" in r["results"][0]
    laatste = api(client, "/core/users/", page=3, page_size=2).json()
    assert laatste["pagination"]["next"] == 0
    assert api(client, "/core/users/", page=9).status_code == 404
    groepen = api(client, "/core/groups/", include_users="true").json()["results"]
    assert {g["name"] for g in groepen} >= {"hub-beheerders", "hub-lezers"} and "users_obj" in groepen[0]
    prov = api(client, "/providers/oauth2/").json()["results"][0]
    assert prov["client_id"] == "hub-sim-client" and prov["grant_types"] == ["authorization_code", "refresh_token"]
    for pad in ("/core/applications/", "/flows/instances/", "/propertymappings/provider/scope/",
                "/crypto/certificatekeypairs/", "/core/brands/", "/managed/blueprints/", "/outposts/instances/",
                "/policies/bindings/", "/core/authenticated_sessions/", "/events/events/"):
        body = api(client, pad).json()
        assert "pagination" in body and isinstance(body["results"], list), pad
    ev = api(client, "/events/events/", actions="login_failed").json()["results"]
    assert [e["action"] for e in ev] == ["login_failed"]
    assert api(client, "/authenticators/admin/all/", user=2).json()[0]["confirmed"] is True
    export = api(client, "/flows/instances/default-authentication-flow/export/")
    assert export.status_code == 200 and "slug: default-authentication-flow" in export.text


def test_older_version_ignores_unknown_filter_and_lacks_grant_types() -> None:
    with TestClient(create_app(versie="2025.6.4")) as c:
        assert "grant_types" not in api(c, "/providers/oauth2/").json()["results"][0]
        alle = api(c, "/events/events/", actions="login_failed").json()["results"]
        assert {e["action"] for e in alle} == {"login", "login_failed"}  # filter genegeerd (zoals DRF)


def test_api_writes_need_the_write_token_and_are_logged(client: TestClient) -> None:
    s = client.get("/_sim/sleutels").json()
    body = {"name": "Nieuwe app", "authorization_flow": "x", "invalidation_flow": "y",
            "redirect_uris": [{"matching_mode": "strict", "url": "https://app.sim.invalid/cb"}]}
    lees_hdr = {"Authorization": f"Bearer {s['api_lees_token']}"}
    lees = client.post("/api/v3/providers/oauth2/", json=body, headers=lees_hdr)
    assert lees.status_code == 403
    schrijf = {"Authorization": f"Bearer {s['api_schrijf_token']}"}
    assert client.post("/api/v3/providers/oauth2/", json={"name": "x"}, headers=schrijf).status_code == 400
    prov = client.post("/api/v3/providers/oauth2/", json=body, headers=schrijf)
    assert prov.status_code == 201 and prov.json()["client_secret"]
    appl = client.post("/api/v3/core/applications/", json={"name": "Nieuwe app", "slug": "nieuw",
                                                           "provider": prov.json()["pk"]}, headers=schrijf)
    assert appl.status_code == 201
    assert client.post("/api/v3/core/applications/", json={"name": "Dubbel", "slug": "nieuw"},
                       headers=schrijf).status_code == 400
    groep = api(client, "/core/groups/").json()["results"][1]["pk"]
    bind = client.post("/api/v3/policies/bindings/", json={"target": appl.json()["pk"], "group": groep},
                       headers=schrijf)
    assert bind.status_code == 201
    assert len(api(client, "/policies/bindings/", target=appl.json()["pk"]).json()["results"]) == 1
    sessie = api(client, "/core/authenticated_sessions/").json()["results"][0]["uuid"]
    assert client.delete(f"/api/v3/core/authenticated_sessions/{sessie}/", headers=schrijf).status_code == 204
    assert client.get("/application/o/nieuw/jwks/").status_code == 200
    log = client.get("/_sim/verzoeken").json()
    schrijvend = [v for v in log if v["methode"] != "GET" and v["pad"].startswith("/api/v3/")]
    assert len(schrijvend) == 7 and s["api_schrijf_token"] not in json.dumps(log)


def test_permission_fault_on_one_part(client: TestClient) -> None:
    client.post("/_sim/fouten", json={"naam": "geen_rechten", "pad": "^/api/v3/events/"})
    assert api(client, "/events/events/").status_code == 403
    assert api(client, "/core/users/").status_code == 200
