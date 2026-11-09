"""Authentik-simulator (alleen tests). Protocol en bronnen: ``e2e/simulators/authentik/README.md``.

Volgt ``goauthentik/authentik`` **version/2026.8.5** (``4ec0d19``) voor twee delen:

1. **OIDC-provider** (wat de Hub gebruikt, B01-006/I09-09-04): discovery
   ``/application/o/<slug>/.well-known/openid-configuration``, ``/application/o/authorize/`` (Authorization Code +
   PKCE S256/plain, ``state``, ``nonce``, exacte redirect-URI), ``/application/o/token/`` (authorization_code en
   refresh_token; client_secret_basic of client_secret_post), ``/application/o/userinfo/``,
   ``/application/o/<slug>/jwks/`` (RS256), ``/application/o/introspect/``, ``/application/o/revoke/`` en
   ``/application/o/<slug>/end-session/``.
2. **Beheer-API** ``/api/v3`` (wat home-control-hub leest en plant, B13): versie, ``core/users/me``, de gepagineerde
   lijsten (providers, applicaties, groepen, gebruikers, flows, scope-mappings, sleutels, brands, blueprints,
   outposts, bindings, events, sessies), authenticators per gebruiker, flow-export, en de schrijfroutes uit de
   voorstellen van home-control-hub (provider/applicatie/binding aanmaken of wijzigen, sessie intrekken) — die
   alleen met een token met schrijfrecht.

Gezondheid: ``/-/health/live/`` (200) en ``/-/health/ready/`` (200 of 503), zoals ``authentik/root/monitoring.py``.
"""

from __future__ import annotations

import base64
import hashlib
import secrets
import threading
import time
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import parse_qs, urlencode

import jwt
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, PlainTextResponse, RedirectResponse, Response
from simkit import ALGEMENE_FOUTEN, Fouten, Verzoeklog, install

VERSIE = "2026.8.5"
NS = uuid.UUID("2c9d6f0e-5a5e-4b8a-9e43-6a1c0de0a7a7")
ACR = "goauthentik.io/providers/oauth2/default"
CODE_TTL_S = 60  # access_code_validity minutes=1
ACCESS_TTL_S = 300  # access_token_validity minutes=5
REFRESH_TTL_S = 30 * 86400

FOUTEN = {
    **ALGEMENE_FOUTEN,
    "niet_gereed": "/-/health/ready/ geeft 503 (database niet bereikbaar).",
    "geen_rechten": "Beheer-API: 403 'You do not have permission…' (gebruik 'pad' voor één onderdeel).",
    "discovery_down": "Discovery-document geeft 500.",
    "jwks_down": "JWKS geeft 500.",
    "token_500": "Token-endpoint geeft 500.",
    "userinfo_401": "Userinfo weigert elk access-token (401).",
    "issuer_fout": "ID-token heeft een andere 'iss' dan de discovery.",
    "aud_fout": "ID-token heeft een andere 'aud' dan de client_id.",
    "verlopen": "ID- en access-token zijn al verlopen bij uitgifte.",
    "handtekening_fout": "ID-token ondertekend met een sleutel die niet in de JWKS staat (zelfde kid).",
    "kid_onbekend": "ID-token met een kid die niet in de JWKS staat.",
    "alg_none": "ID-token zonder handtekening (alg 'none').",
    "nonce_fout": "ID-token met een andere nonce dan gevraagd.",
    "groepen_ontbreken": "Geen 'groups'-claim in ID-token en userinfo.",
    "sleutelrotatie": "Er wordt met een nieuwe sleutel ondertekend; de JWKS toont alleen de nieuwe sleutel.",
}


def _id(naam: str) -> str:
    return str(uuid.uuid5(NS, naam))


def _iso(t: datetime) -> str:
    return t.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _lijn(versie: str) -> tuple[int, int]:
    j, m, *_ = versie.split(".")
    return int(j), int(m)


def _b64(n: int) -> str:
    raw = n.to_bytes((n.bit_length() + 7) // 8, "big")
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


@dataclass
class Sleutel:
    kid: str
    prive: rsa.RSAPrivateKey

    def jwk(self) -> dict[str, Any]:
        pub = self.prive.public_key().public_numbers()
        return {"kty": "RSA", "alg": "RS256", "use": "sig", "kid": self.kid, "n": _b64(pub.n), "e": _b64(pub.e)}


def _nieuwe_sleutel() -> Sleutel:
    prive = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    return Sleutel(secrets.token_hex(16), prive)


@dataclass
class State:
    versie: str = VERSIE
    lees_token: str = ""
    schrijf_token: str = ""
    sleutels: list[Sleutel] = field(default_factory=list)
    vervalste_sleutel: Sleutel | None = None
    ingelogd: str | None = "anna"
    gebruikers: dict[str, dict[str, Any]] = field(default_factory=dict)
    groepen: dict[str, dict[str, Any]] = field(default_factory=dict)
    providers: list[dict[str, Any]] = field(default_factory=list)
    applicaties: list[dict[str, Any]] = field(default_factory=list)
    bindings: list[dict[str, Any]] = field(default_factory=list)
    flows: list[dict[str, Any]] = field(default_factory=list)
    scopes: list[dict[str, Any]] = field(default_factory=list)
    certs: list[dict[str, Any]] = field(default_factory=list)
    sessies: list[dict[str, Any]] = field(default_factory=list)
    events: list[dict[str, Any]] = field(default_factory=list)
    codes: dict[str, dict[str, Any]] = field(default_factory=dict)
    access: dict[str, dict[str, Any]] = field(default_factory=dict)
    refresh: dict[str, dict[str, Any]] = field(default_factory=dict)
    lock: threading.Lock = field(default_factory=threading.Lock)

    def seed(self) -> None:
        self.lees_token = "ak-sim-lees-" + secrets.token_urlsafe(24)
        self.schrijf_token = "ak-sim-schrijf-" + secrets.token_urlsafe(24)
        self.sleutels = [_nieuwe_sleutel()]
        self.vervalste_sleutel = _nieuwe_sleutel()
        self.ingelogd = "anna"
        self.codes, self.access, self.refresh = {}, {}, {}
        self.flows = [self._flow(s, d) for s, d in (
            ("default-authentication-flow", "authentication"),
            ("default-provider-authorization-explicit-consent", "authorization"),
            ("default-provider-authorization-implicit-consent", "authorization"),
            ("default-provider-invalidation-flow", "invalidation"),
            ("default-recovery-flow", "recovery"),
        )]
        self.scopes = [
            {"pk": _id("scope-" + s), "managed": f"goauthentik.io/providers/oauth2/scope-{s}", "scope_name": s,
             "name": f"authentik default OAuth Mapping: OpenID '{s}'", "description": "", "expression": e}
            for s, e in (("openid", "return {}"), ("email", "return {'email': request.user.email}"),
                         ("profile", "return {'groups': [g.name for g in request.user.ak_groups.all()]}"),
                         ("offline_access", "return {}"))
        ]
        self.certs = [{"pk": _id("cert-self-signed"), "name": "authentik Self-signed Certificate",
                       "fingerprint_sha256": "sim", "cert_expiry": "2027-11-09T00:00:00Z",
                       "cert_subject": "CN=authentik",
                       "private_key_available": True, "key_type": "rsa", "managed": None}]
        self.gebruikers = {}
        for pk, naam in enumerate(("akadmin", "anna", "bram", "carla", "nood-ak"), start=1):
            self.gebruikers[naam] = {"pk": pk, "username": naam, "name": naam.title(), "is_active": True,
                                     "email": f"{naam}@sim.invalid", "type": "internal", "uuid": _id("user-" + naam),
                                     "attributes": {}, "path": "users"}
        self.gebruikers["hch-lezer"] = {"pk": 90, "username": "hch-lezer", "name": "home-control-hub (lezen)",
                                        "is_active": True, "email": "", "type": "service_account",
                                        "uuid": _id("user-hch"), "attributes": {}, "path": "users"}
        self.groepen = {}
        for naam, superuser, leden in (("authentik Admins", True, ["akadmin"]), ("hub-beheerders", False, ["anna"]),
                                       ("hub-operators", False, ["bram"]), ("hub-lezers", False, ["carla"])):
            self.groepen[naam] = {"pk": _id("group-" + naam), "num_pk": len(self.groepen) + 1, "name": naam,
                                  "is_superuser": superuser, "parent": None, "attributes": {}, "leden": leden}
        self.providers, self.applicaties, self.bindings = [], [], []
        self.voeg_client_toe("hub", "Hub", "hub-sim-client", "https://hub.sim.invalid/api/v1/auth/oidc/callback")
        now = datetime.now(UTC).replace(microsecond=0)
        self.sessies = [{"uuid": _id("sessie-anna"), "user": 2, "last_ip": "sim", "last_used": _iso(now),
                         "expires": _iso(now + timedelta(hours=8))}]
        self.events = [self._event("login", "anna", now - timedelta(minutes=5)),
                       self._event("login_failed", "onbekend", now - timedelta(minutes=3))]

    def _flow(self, slug: str, designation: str) -> dict[str, Any]:
        return {"pk": _id("flow-" + slug), "slug": slug, "name": slug.replace("-", " ").title(), "title": slug,
                "designation": designation, "policy_engine_mode": "any"}

    def _event(self, action: str, user: str, t: datetime) -> dict[str, Any]:
        g = self.gebruikers.get(user)
        return {"pk": str(uuid.uuid4()), "action": action, "app": "authentik.events.signals",
                "user": {"pk": g["pk"], "username": user} if g else {"pk": -1, "username": "AnonymousUser"},
                "context": {"username": user} if action == "login_failed" else {}, "client_ip": "sim",
                "created": _iso(t), "expires": _iso(t + timedelta(days=365))}

    def voeg_client_toe(self, slug: str, naam: str, client_id: str, redirect: str) -> dict[str, Any]:
        pk = 10 + len(self.providers)
        prov = {"pk": pk, "name": naam, "authorization_flow": self.flows[2]["pk"],
                "invalidation_flow": self.flows[3]["pk"], "property_mappings": [s["pk"] for s in self.scopes],
                "client_type": "confidential", "client_id": client_id, "client_secret": secrets.token_urlsafe(32),
                "access_code_validity": "minutes=1", "access_token_validity": "minutes=5",
                "refresh_token_validity": "days=30", "include_claims_in_id_token": True,
                "signing_key": self.certs[0]["pk"], "redirect_uris": [{"matching_mode": "strict", "url": redirect}],
                "sub_mode": "hashed_user_id", "issuer_mode": "per_provider", "component": "ak-provider-oauth2-form"}
        if _lijn(self.versie) >= (2026, 5):
            prov["grant_types"] = ["authorization_code", "refresh_token"]
        self.providers.append(prov)
        self.applicaties.append({"pk": _id("app-" + slug), "name": naam, "slug": slug, "provider": pk,
                                 "meta_launch_url": "", "policy_engine_mode": "any", "group": "",
                                 "open_in_new_tab": False})
        return prov


def _fout(status: int, detail: str) -> dict[str, Any]:
    return {"detail": detail}


def _oauth_fout(code: str, beschrijving: str, status: int = 400) -> JSONResponse:
    return JSONResponse({"error": code, "error_description": beschrijving}, status_code=status,
                        headers={"Cache-Control": "no-store"})


def create_app(state: State | None = None, versie: str = VERSIE) -> FastAPI:
    st = state or State(versie=versie)
    st.seed()
    fouten = Fouten(dict(FOUTEN))
    log = Verzoeklog()
    app = FastAPI(title="Authentik-simulator", version=st.versie, docs_url=None, redoc_url=None, openapi_url=None)
    app.state.sim = st
    app.state.fouten = fouten
    app.state.log = log

    def basis(request: Request) -> str:
        return f"{request.url.scheme}://{request.url.netloc}"

    def app_en_provider(slug: str) -> tuple[dict[str, Any], dict[str, Any]] | None:
        a = next((x for x in st.applicaties if x["slug"] == slug), None)
        p = next((x for x in st.providers if a and x["pk"] == a["provider"]), None)
        return (a, p) if a and p else None

    def provider_van_client(client_id: str) -> tuple[dict[str, Any], dict[str, Any]] | None:
        p = next((x for x in st.providers if x["client_id"] == client_id), None)
        a = next((x for x in st.applicaties if p and x["provider"] == p["pk"]), None)
        return (a, p) if a and p else None

    def issuer(request: Request, slug: str) -> str:
        return f"{basis(request)}/application/o/{slug}/"

    def actieve_sleutel() -> Sleutel:
        if fouten.is_aan("sleutelrotatie") and len(st.sleutels) == 1:
            st.sleutels.append(_nieuwe_sleutel())
        return st.sleutels[-1]

    def jwks_sleutels() -> list[Sleutel]:
        actieve_sleutel()
        return st.sleutels[-1:] if fouten.is_aan("sleutelrotatie") else st.sleutels[:1]

    def sub_van(g: dict[str, Any]) -> str:
        return hashlib.sha256(f"{g['pk']}-{g['uuid']}".encode()).hexdigest()

    def groepen_van(naam: str) -> list[str]:
        return [g["name"] for g in st.groepen.values() if naam in g["leden"]]

    def claims_voor(g: dict[str, Any], scopes: set[str]) -> dict[str, Any]:
        c: dict[str, Any] = {"sub": sub_van(g)}
        if "email" in scopes:
            c |= {"email": g["email"], "email_verified": True}
        if "profile" in scopes:
            c |= {"name": g["name"], "given_name": g["name"], "preferred_username": g["username"],
                  "nickname": g["username"]}
            if not fouten.is_aan("groepen_ontbreken"):
                c["groups"] = groepen_van(g["username"])
        return c

    def onderteken(claims: dict[str, Any]) -> str:
        if fouten.is_aan("alg_none"):
            return jwt.encode(claims, key="", algorithm="none")
        sleutel = actieve_sleutel()
        kid = sleutel.kid
        if fouten.is_aan("kid_onbekend"):
            kid = "onbekend-" + kid
        if fouten.is_aan("handtekening_fout") and st.vervalste_sleutel is not None:
            sleutel = st.vervalste_sleutel
        return jwt.encode(claims, sleutel.prive, algorithm="RS256", headers={"kid": kid, "typ": "JWT"})

    def tokens_uitgeven(request: Request, slug: str, prov: dict[str, Any], gebruiker: str, scopes: set[str],
                        nonce: str | None, auth_time: int) -> dict[str, Any]:
        g = st.gebruikers[gebruiker]
        nu = int(time.time())
        exp = nu - 10 if fouten.is_aan("verlopen") else nu + ACCESS_TTL_S
        iss = issuer(request, slug) + ("anders/" if fouten.is_aan("issuer_fout") else "")
        aud = prov["client_id"] + ("-anders" if fouten.is_aan("aud_fout") else "")
        access = secrets.token_urlsafe(32)
        st.access[access] = {"gebruiker": gebruiker, "scopes": scopes, "client_id": prov["client_id"], "exp": exp,
                             "iat": nu, "slug": slug}
        uit: dict[str, Any] = {"access_token": access, "token_type": "Bearer", "expires_in": exp - nu,
                               "scope": " ".join(sorted(scopes))}
        if "openid" in scopes:
            claims = {"iss": iss, "aud": aud, "exp": exp, "iat": nu, "auth_time": auth_time, "acr": ACR,
                      "amr": ["pwd"], "sid": hashlib.sha256(access.encode()).hexdigest()[:32]}
            if nonce is not None:
                claims["nonce"] = "andere-nonce" if fouten.is_aan("nonce_fout") else nonce
            claims["at_hash"] = base64.urlsafe_b64encode(hashlib.sha256(access.encode()).digest()[:16]).rstrip(
                b"=").decode()
            claims |= claims_voor(g, scopes)
            uit["id_token"] = onderteken(claims)
        # Authentik geeft bij de code-flow standaard ook een refresh-token (ook zonder offline_access)
        rt = secrets.token_urlsafe(32)
        st.refresh[rt] = {"gebruiker": gebruiker, "scopes": scopes, "client_id": prov["client_id"], "slug": slug,
                          "exp": nu + REFRESH_TTL_S, "auth_time": auth_time}
        uit["refresh_token"] = rt
        return uit

    # ------------------------------------------------------------------ gezondheid
    @app.get("/-/health/live/")
    def live() -> Response:
        return Response(status_code=200)

    @app.get("/-/health/ready/")
    def ready() -> Response:
        return Response(status_code=503 if fouten.is_aan("niet_gereed") else 200)

    # ------------------------------------------------------------------ OIDC
    @app.get("/application/o/{slug}/.well-known/openid-configuration")
    def discovery(slug: str, request: Request) -> JSONResponse:
        if fouten.is_aan("discovery_down"):
            return JSONResponse({"detail": "Interne fout"}, status_code=500)
        gevonden = app_en_provider(slug)
        if gevonden is None:
            return JSONResponse({"detail": "Not found."}, status_code=404)
        b = basis(request)
        return JSONResponse({
            "issuer": issuer(request, slug),
            "authorization_endpoint": f"{b}/application/o/authorize/",
            "token_endpoint": f"{b}/application/o/token/",
            "userinfo_endpoint": f"{b}/application/o/userinfo/",
            "end_session_endpoint": f"{b}/application/o/{slug}/end-session/",
            "introspection_endpoint": f"{b}/application/o/introspect/",
            "revocation_endpoint": f"{b}/application/o/revoke/",
            "device_authorization_endpoint": f"{b}/application/o/device/",
            "jwks_uri": f"{b}/application/o/{slug}/jwks/",
            "response_types_supported": ["code", "id_token", "id_token token", "code token", "code id_token",
                                         "code id_token token"],
            "response_modes_supported": ["query", "fragment", "form_post"],
            "grant_types_supported": ["authorization_code", "refresh_token", "implicit", "client_credentials",
                                      "password", "urn:ietf:params:oauth:grant-type:device_code"],
            "id_token_signing_alg_values_supported": ["RS256"],
            "subject_types_supported": ["public"],
            "token_endpoint_auth_methods_supported": ["client_secret_post", "client_secret_basic"],
            "acr_values_supported": [ACR],
            "scopes_supported": [s["scope_name"] for s in st.scopes],
            "request_parameter_supported": False,
            "claims_supported": ["sub", "iss", "aud", "exp", "iat", "auth_time", "acr", "amr", "nonce", "email",
                                 "email_verified", "name", "given_name", "preferred_username", "nickname", "groups"],
            "claims_parameter_supported": False,
            "code_challenge_methods_supported": ["plain", "S256"],
        })

    @app.get("/application/o/{slug}/jwks/")
    def jwks(slug: str) -> JSONResponse:
        if fouten.is_aan("jwks_down"):
            return JSONResponse({"detail": "Interne fout"}, status_code=500)
        if app_en_provider(slug) is None:
            return JSONResponse({"detail": "Not found."}, status_code=404)
        return JSONResponse({"keys": [k.jwk() for k in jwks_sleutels()]})

    @app.get("/application/o/authorize/")
    def authorize(request: Request) -> Response:
        q = request.query_params
        gevonden = provider_van_client(q.get("client_id", ""))
        if gevonden is None:
            return JSONResponse({"error": "invalid_client", "error_description": "Onbekende client_id."},
                                status_code=400)
        appl, prov = gevonden
        redirect = q.get("redirect_uri", "")
        if redirect not in [r["url"] for r in prov["redirect_uris"]]:
            # nooit doorsturen naar een niet-geregistreerde URI
            return JSONResponse({"error": "invalid_request", "error_description": "Redirect URI Error"},
                                status_code=400)
        state = q.get("state")

        def terug(params: dict[str, str]) -> RedirectResponse:
            if state is not None:
                params["state"] = state
            return RedirectResponse(f"{redirect}?{urlencode(params)}", status_code=302)

        if q.get("response_type") != "code":
            return terug({"error": "unsupported_response_type", "error_description": "Alleen 'code'."})
        scopes = set(q.get("scope", "").split())
        if "openid" not in scopes:
            return terug({"error": "invalid_scope", "error_description": "'openid' ontbreekt."})
        methode = q.get("code_challenge_method", "plain")
        if q.get("code_challenge") and methode not in ("plain", "S256"):
            return terug({"error": "invalid_request", "error_description": "Onbekende code_challenge_method."})
        if st.ingelogd is None:
            return RedirectResponse(f"/if/flow/default-authentication-flow/?{urlencode({'next': str(request.url)})}",
                                    status_code=302)
        code = secrets.token_urlsafe(24)
        st.codes[code] = {"client_id": prov["client_id"], "redirect_uri": redirect, "scopes": scopes,
                          "nonce": q.get("nonce"), "challenge": q.get("code_challenge"), "methode": methode,
                          "gebruiker": st.ingelogd, "exp": time.time() + CODE_TTL_S, "slug": appl["slug"],
                          "auth_time": int(time.time())}
        return terug({"code": code})

    def client_auth(request: Request, form: dict[str, str]) -> tuple[dict[str, Any], dict[str, Any]] | None:
        auth = request.headers.get("authorization", "")
        cid, secret = form.get("client_id", ""), form.get("client_secret", "")
        if auth.startswith("Basic "):
            try:
                cid, _, secret = base64.b64decode(auth[6:]).decode().partition(":")
            except (ValueError, UnicodeDecodeError):
                return None
        gevonden = provider_van_client(cid)
        if gevonden is None or not secrets.compare_digest(secret, gevonden[1]["client_secret"]):
            return None
        return gevonden

    async def formulier(request: Request) -> dict[str, str]:
        raw = (await request.body()).decode("utf-8", errors="replace")
        return {k: v[0] for k, v in parse_qs(raw, keep_blank_values=True).items()}

    @app.post("/application/o/token/")
    async def token(request: Request) -> JSONResponse:
        if fouten.is_aan("token_500"):
            return JSONResponse({"detail": "Interne fout"}, status_code=500)
        form = await formulier(request)
        gevonden = client_auth(request, form)
        if gevonden is None:
            return _oauth_fout("invalid_client", "Client authentication failed.", 401)
        appl, prov = gevonden
        grant = form.get("grant_type")
        with st.lock:
            if grant == "authorization_code":
                code = st.codes.pop(form.get("code", ""), None)  # eenmalig: hergebruik → invalid_grant
                if code is None or code["exp"] < time.time() or code["client_id"] != prov["client_id"]:
                    return _oauth_fout("invalid_grant", "The provided authorization grant or refresh token is "
                                                        "invalid, expired, revoked …")
                if form.get("redirect_uri") != code["redirect_uri"]:
                    return _oauth_fout("invalid_grant", "redirect_uri komt niet overeen.")
                if code["challenge"]:
                    verifier = form.get("code_verifier", "")
                    if code["methode"] == "S256":
                        berekend = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(
                            b"=").decode()
                    else:
                        berekend = verifier
                    if not verifier or not secrets.compare_digest(berekend, code["challenge"]):
                        return _oauth_fout("invalid_grant", "PKCE-controle mislukt (code_verifier).")
                uit = tokens_uitgeven(request, code["slug"], prov, code["gebruiker"], code["scopes"], code["nonce"],
                                      code["auth_time"])
            elif grant == "refresh_token":
                rt = st.refresh.pop(form.get("refresh_token", ""), None)  # rotatie: oud refresh-token vervalt
                if rt is None or rt["exp"] < time.time() or rt["client_id"] != prov["client_id"]:
                    return _oauth_fout("invalid_grant", "Refresh-token ongeldig of ingetrokken.")
                uit = tokens_uitgeven(request, rt["slug"], prov, rt["gebruiker"], rt["scopes"], None, rt["auth_time"])
            else:
                return _oauth_fout("unsupported_grant_type", "De simulator ondersteunt alleen authorization_code en "
                                                             "refresh_token (wat de Hub gebruikt).")
        _ = appl
        return JSONResponse(uit, headers={"Cache-Control": "no-store", "Pragma": "no-cache"})

    @app.api_route("/application/o/userinfo/", methods=["GET", "POST"])
    def userinfo(request: Request) -> JSONResponse:
        auth = request.headers.get("authorization", "")
        item = st.access.get(auth[len("Bearer "):]) if auth.startswith("Bearer ") else None
        if fouten.is_aan("userinfo_401") or item is None or item["exp"] < time.time():
            return JSONResponse({"error": "invalid_token"}, status_code=401,
                                headers={"WWW-Authenticate": 'error="invalid_token"'})
        return JSONResponse(claims_voor(st.gebruikers[item["gebruiker"]], item["scopes"]))

    @app.post("/application/o/introspect/")
    async def introspect(request: Request) -> JSONResponse:
        form = await formulier(request)
        if client_auth(request, form) is None:
            return _oauth_fout("invalid_client", "Client authentication failed.", 401)
        item = st.access.get(form.get("token", ""))
        if item is None or item["exp"] < time.time():
            return JSONResponse({"active": False})
        g = st.gebruikers[item["gebruiker"]]
        return JSONResponse({"active": True, "scope": " ".join(sorted(item["scopes"])), "client_id": item["client_id"],
                             "sub": sub_van(g), "exp": item["exp"], "iat": item["iat"], "token_type": "access_token",
                             "iss": issuer(request, item["slug"])})

    @app.post("/application/o/revoke/")
    async def revoke(request: Request) -> Response:
        form = await formulier(request)
        if client_auth(request, form) is None:
            return _oauth_fout("invalid_client", "Client authentication failed.", 401)
        tok = form.get("token", "")
        with st.lock:
            st.access.pop(tok, None)
            st.refresh.pop(tok, None)
        return Response(status_code=200)

    @app.get("/application/o/{slug}/end-session/")
    def end_session(slug: str, request: Request) -> Response:
        gevonden = app_en_provider(slug)
        if gevonden is None:
            return JSONResponse({"detail": "Not found."}, status_code=404)
        st.ingelogd = None
        doel = request.query_params.get("post_logout_redirect_uri")
        if doel and doel in [r["url"] for r in gevonden[1]["redirect_uris"]]:
            return RedirectResponse(doel, status_code=302)
        return PlainTextResponse("Afgemeld (simulator).")

    # ------------------------------------------------------------------ beheer-API /api/v3
    def api_toegang(request: Request, schrijven: bool = False) -> JSONResponse | None:
        auth = request.headers.get("authorization", "")
        if not auth:
            return JSONResponse(_fout(403, "Authentication credentials were not provided."), status_code=403)
        tok = auth[len("Bearer "):] if auth.startswith("Bearer ") else ""
        geldig = {st.lees_token, st.schrijf_token}
        if tok not in geldig:
            return JSONResponse(_fout(403, "Token invalid/expired"), status_code=403)
        if fouten.is_aan("geen_rechten", request.url.path) or (schrijven and tok != st.schrijf_token):
            return JSONResponse(_fout(403, "You do not have permission to perform this action."), status_code=403)
        return None

    def pagina(request: Request, items: list[dict[str, Any]]) -> JSONResponse:
        q = request.query_params
        try:
            page = max(1, int(q.get("page", "1")))
            size = max(1, min(int(q.get("page_size", "20")), 100))
        except ValueError:
            return JSONResponse({"detail": "Invalid page."}, status_code=404)
        totaal = len(items)
        pages = max(1, (totaal + size - 1) // size)
        if page > pages:
            return JSONResponse({"detail": "Invalid page."}, status_code=404)
        deel = items[(page - 1) * size: page * size]
        return JSONResponse({"pagination": {"next": page + 1 if page < pages else 0,
                                            "previous": page - 1 if page > 1 else 0, "count": totaal,
                                            "current": page, "total_pages": pages,
                                            "start_index": (page - 1) * size + 1 if deel else 0,
                                            "end_index": (page - 1) * size + len(deel)},
                             "results": deel, "autocomplete": {}})

    def gebruiker_view(g: dict[str, Any], met_groepen: bool) -> dict[str, Any]:
        v = {k: g[k] for k in ("pk", "username", "name", "is_active", "email", "type", "uuid", "attributes", "path")}
        v["is_superuser"] = any(gr["is_superuser"] for gr in st.groepen.values() if g["username"] in gr["leden"])
        v["groups"] = [gr["pk"] for gr in st.groepen.values() if g["username"] in gr["leden"]]
        if met_groepen:
            v["groups_obj"] = [{"pk": gr["pk"], "name": gr["name"], "is_superuser": gr["is_superuser"]}
                               for gr in st.groepen.values() if g["username"] in gr["leden"]]
        return v

    def groep_view(gr: dict[str, Any], met_gebruikers: bool) -> dict[str, Any]:
        v = {k: gr[k] for k in ("pk", "num_pk", "name", "is_superuser", "parent", "attributes")}
        v["users"] = [st.gebruikers[n]["pk"] for n in gr["leden"] if n in st.gebruikers]
        if met_gebruikers:
            v["users_obj"] = [{"pk": st.gebruikers[n]["pk"], "username": n, "name": st.gebruikers[n]["name"],
                               "is_active": True} for n in gr["leden"] if n in st.gebruikers]
        return v

    @app.get("/api/v3/admin/version/")
    def admin_version(request: Request) -> JSONResponse:
        if (weiger := api_toegang(request)) is not None:
            return weiger
        return JSONResponse({"version_current": st.versie, "version_latest": st.versie, "version_latest_valid": True,
                             "build_hash": "sim", "outdated": False, "outpost_outdated": False})

    @app.get("/api/v3/core/users/me/")
    def users_me(request: Request) -> JSONResponse:
        if (weiger := api_toegang(request)) is not None:
            return weiger
        g = gebruiker_view(st.gebruikers["hch-lezer"], False)
        g["system_permissions"] = ["view_system_info"]
        return JSONResponse({"user": g})

    @app.get("/api/v3/{pad:path}")
    def api_lezen(pad: str, request: Request) -> Response:
        if (weiger := api_toegang(request)) is not None:
            return weiger
        q = request.query_params
        sub = "/" + pad
        if sub.startswith("/flows/instances/") and sub.endswith("/export/"):
            slug = sub[len("/flows/instances/"):-len("/export/")]
            if not any(f["slug"] == slug for f in st.flows):
                return JSONResponse({"detail": "Not found."}, status_code=404)
            return PlainTextResponse(f"version: 1\nmetadata:\n  name: {slug}\nentries:\n- model: authentik_flows.flow\n"
                                     f"  identifiers:\n    slug: {slug}\n", media_type="text/yaml")
        if sub == "/authenticators/admin/all/":
            return JSONResponse([{"pk": "1", "name": "Telefoon", "type": "authentik_stages_authenticator_totp."
                                 "totpdevice", "confirmed": True}] if q.get("user") in ("1", "2") else [])
        lijsten: dict[str, Any] = {
            "/providers/oauth2/": lambda: st.providers,
            "/core/applications/": lambda: st.applicaties,
            "/core/groups/": lambda: [groep_view(g, q.get("include_users") == "true") for g in st.groepen.values()],
            "/core/users/": lambda: [gebruiker_view(g, q.get("include_groups") == "true") for g in
                                     st.gebruikers.values() if not q.get("username") or g["username"] ==
                                     q.get("username")],
            "/flows/instances/": lambda: st.flows,
            "/propertymappings/provider/scope/": lambda: st.scopes,
            "/crypto/certificatekeypairs/": lambda: st.certs,
            "/core/brands/": lambda: [{"brand_uuid": _id("brand"), "domain": "authentik-default", "default": True,
                                       "flow_authentication": st.flows[0]["pk"]}],
            "/managed/blueprints/": lambda: [{"pk": _id("bp"), "name": "System - OAuth2 Provider - Scopes",
                                              "path": "system/providers-oauth2.yaml", "enabled": True,
                                              "status": "successful"}],
            "/outposts/instances/": lambda: [{"pk": _id("outpost"), "name": "authentik Embedded Outpost",
                                              "type": "proxy", "providers": [],
                                              "managed": "goauthentik.io/outposts/embedded"}],
            "/policies/bindings/": lambda: [b for b in st.bindings if not q.get("target") or b["target"] ==
                                            q.get("target")],
            "/core/authenticated_sessions/": lambda: [s for s in st.sessies if not q.get("user__username") or
                                                      st.gebruikers.get(q.get("user__username") or "", {}).get("pk")
                                                      == s["user"]],
            "/events/events/": lambda: events(q),
        }
        maker = lijsten.get(sub)
        if maker is None:
            return JSONResponse({"detail": "Not found."}, status_code=404)
        return pagina(request, list(maker()))

    def events(q: Any) -> list[dict[str, Any]]:
        items = list(st.events)
        acties = q.get("actions")
        if acties and _lijn(st.versie) >= (2025, 8):  # oudere versies negeren een onbekende filter (zoals DRF)
            gekozen = set(acties.split(","))
            items = [e for e in items if e["action"] in gekozen]
        if q.get("action"):
            items = [e for e in items if e["action"] == q.get("action")]
        return sorted(items, key=lambda e: e["created"], reverse=q.get("ordering", "-created").startswith("-"))

    # schrijfroutes uit de voorstellen van home-control-hub (alleen met schrijftoken)
    @app.post("/api/v3/providers/oauth2/")
    async def maak_provider(request: Request) -> JSONResponse:
        if (weiger := api_toegang(request, schrijven=True)) is not None:
            return weiger
        body = await request.json()
        fout = {k: ["This field is required."] for k in ("name", "authorization_flow", "invalidation_flow")
                if not body.get(k)}
        if fout:
            return JSONResponse(fout, status_code=400)
        with st.lock:
            pk = 10 + len(st.providers)
            prov = {**body, "pk": pk, "client_id": body.get("client_id") or secrets.token_urlsafe(16),
                    "client_secret": body.get("client_secret") or secrets.token_urlsafe(32)}
            st.providers.append(prov)
        return JSONResponse(prov, status_code=201)

    @app.patch("/api/v3/providers/oauth2/{pk}/")
    async def wijzig_provider(pk: int, request: Request) -> JSONResponse:
        if (weiger := api_toegang(request, schrijven=True)) is not None:
            return weiger
        body = await request.json()
        with st.lock:
            prov = next((p for p in st.providers if p["pk"] == pk), None)
            if prov is None:
                return JSONResponse({"detail": "Not found."}, status_code=404)
            prov.update({k: v for k, v in body.items() if k != "pk"})
            return JSONResponse(prov)

    @app.post("/api/v3/core/applications/")
    async def maak_applicatie(request: Request) -> JSONResponse:
        if (weiger := api_toegang(request, schrijven=True)) is not None:
            return weiger
        body = await request.json()
        with st.lock:
            if any(a["slug"] == body.get("slug") for a in st.applicaties):
                return JSONResponse({"slug": ["Application with this slug already exists."]}, status_code=400)
            if not body.get("name") or not body.get("slug"):
                return JSONResponse({"name": ["This field is required."]}, status_code=400)
            appl = {"pk": _id("app-" + str(body["slug"])), "policy_engine_mode": "any", "group": "",
                    "meta_launch_url": "", **body}
            st.applicaties.append(appl)
        return JSONResponse(appl, status_code=201)

    @app.patch("/api/v3/core/applications/{slug}/")
    async def wijzig_applicatie(slug: str, request: Request) -> JSONResponse:
        if (weiger := api_toegang(request, schrijven=True)) is not None:
            return weiger
        body = await request.json()
        with st.lock:
            appl = next((a for a in st.applicaties if a["slug"] == slug), None)
            if appl is None:
                return JSONResponse({"detail": "Not found."}, status_code=404)
            appl.update({k: v for k, v in body.items() if k not in ("pk", "slug")})
            return JSONResponse(appl)

    @app.post("/api/v3/policies/bindings/")
    async def maak_binding(request: Request) -> JSONResponse:
        if (weiger := api_toegang(request, schrijven=True)) is not None:
            return weiger
        body = await request.json()
        if not body.get("target") or not (body.get("group") or body.get("user") or body.get("policy")):
            return JSONResponse({"non_field_errors": ["One of 'group', 'user' or 'policy' must be set."]},
                                status_code=400)
        with st.lock:
            b = {"pk": str(uuid.uuid4()), "enabled": True, "order": 0, "negate": False, "timeout": 30, **body}
            st.bindings.append(b)
        return JSONResponse(b, status_code=201)

    @app.delete("/api/v3/core/authenticated_sessions/{sessie}/")
    def trek_sessie_in(sessie: str, request: Request) -> Response:
        if (weiger := api_toegang(request, schrijven=True)) is not None:
            return weiger
        with st.lock:
            voor = len(st.sessies)
            st.sessies = [s for s in st.sessies if s["uuid"] != sessie]
            if len(st.sessies) == voor:
                return JSONResponse({"detail": "Not found."}, status_code=404)
        return Response(status_code=204)

    # ------------------------------------------------------------------ testbediening
    @app.post("/_sim/oidc/ingelogd", include_in_schema=False)
    async def zet_ingelogd(request: Request) -> JSONResponse:
        """Wie er in de (gesimuleerde) browser bij Authentik is aangemeld; ``null`` = niemand."""
        naam = (await request.json()).get("gebruiker")
        if naam is not None and naam not in st.gebruikers:
            return JSONResponse({"fout": "onbekende gebruiker"}, status_code=422)
        st.ingelogd = naam
        return JSONResponse({"ingelogd": naam})

    @app.post("/_sim/oidc/client", include_in_schema=False)
    async def nieuwe_client(request: Request) -> JSONResponse:
        """Extra OIDC-client (slug, naam, client_id, redirect_uri); geeft het client_secret één keer terug."""
        b = await request.json()
        with st.lock:
            prov = st.voeg_client_toe(str(b["slug"]), str(b.get("naam") or b["slug"]), str(b["client_id"]),
                                      str(b["redirect_uri"]))
        return JSONResponse({"client_id": prov["client_id"], "client_secret": prov["client_secret"]})

    @app.post("/_sim/groep", include_in_schema=False)
    async def zet_groep(request: Request) -> JSONResponse:
        """Leden van een groep zetten (rolmapping testen)."""
        b = await request.json()
        with st.lock:
            gr = st.groepen.setdefault(str(b["naam"]), {"pk": _id("group-" + str(b["naam"])),
                                                        "num_pk": len(st.groepen) + 1, "name": str(b["naam"]),
                                                        "is_superuser": False, "parent": None, "attributes": {},
                                                        "leden": []})
            gr["leden"] = [str(x) for x in b.get("leden", [])]
        return JSONResponse({"naam": gr["name"], "leden": gr["leden"]})

    def sleutels() -> dict[str, Any]:
        hub = next(p for p in st.providers if p["client_id"] == "hub-sim-client")
        return {"api_lees_token": st.lees_token, "api_schrijf_token": st.schrijf_token,
                "hub_client": {"slug": "hub", "client_id": hub["client_id"], "client_secret": hub["client_secret"],
                               "redirect_uri": hub["redirect_uris"][0]["url"]}}

    install(app, fouten=fouten, log=log, reset=st.seed, fout_body=_fout, sleutels=sleutels)
    return app
