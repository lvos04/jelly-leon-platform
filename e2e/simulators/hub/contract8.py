"""Bouwstenen van contract 1.3 §8 voor de test-stand-ins (Hub-simulator en executor-stand-in) — géén productiecode.

Eigen, kleine implementatie van wat de Hub en de executor delen (in het echt het pakket ``asm_policy`` van
ai-server-manager, ``docs/EXECUTION_REQUEST.md`` aldaar): canonieke JSON, de resource-ETag van een doel, de allowlist
met risicoklassen (contracttabel §8), ondertekenen en controleren van een ``ExecutionRequest`` (Ed25519) en de harde
parametergrenzen. Bewust los van de dienst (``lifecycle``): de planner maakt alleen voorstellen, deze code beoordeelt
ze zoals de Hub en de executor dat doen. Sim-getest; tegen de echte Hub/executor NIET GEVALIDEERD.
"""

from __future__ import annotations

import base64
import hashlib
import json
import re
from collections.abc import Iterable, Mapping
from datetime import UTC, datetime
from typing import Any

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey

RISK_ORDER = {"R0": 0, "R1": 1, "R2": 2, "R3": 3}
SANDBOX_DATASET = "asm-sandbox"
IX_APP = "ix-app"
# action_id → (klasse, klasse bij een kritieke app, doelsoort, requires_backup); contract 1.3 §8 allowlist v1
ACTIES: dict[str, tuple[str, str | None, str, bool]] = {
    "app.restart": ("R1", "R2", "app", False),
    "app.stop": ("R1", "R2", "app", False),
    "app.start": ("R1", "R2", "app", False),
    "app.install": ("R2", None, "app_nieuw", False),
    "app.update": ("R2", None, "app", True),
    "app.rollback": ("R2", None, "app", True),
    "app.uninstall": ("R2", None, "app", False),
    "zfs.snapshot.create": ("R1", None, "dataset", False),
    "zfs.snapshot.clone_to_sandbox": ("R1", None, "snapshot", False),
    "notification.send": ("R0", None, "geen", False),
}
KRITIEKE_APPS = frozenset({"ai-server-manager", "ai-server-manager-executor", "home-control-hub", "authentik",
                           "adguard-home", "tailscale", "cloudflared"})
PROPOSAL_KEYS = {"idempotency_key", "action_id", "target", "parameters", "reason", "actor", "expected_version",
                 "risk_claim", "evidence_ids", "expires_in_seconds"}
APPROVAL_KEYS = {"approval_id", "request_id", "preview_hash", "resource_etag", "policy_hash", "risk_class",
                 "approved_by", "approved_at", "expires_at", "one_time_nonce", "backup_checkpoint_id", "action_scope"}
REQUEST_KEYS = {"proposal", "approval", "policy_decision", "issued_at", "expires_at", "nonce", "signature"}
ACTOR_TYPES = {"user", "ai", "flow", "voice", "service", "system"}
APP_NAME_RE = re.compile(r"^[a-z]([-a-z0-9]*[a-z0-9])?$")
DATASET_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]*(/[A-Za-z0-9_.:-]+)*$")
SNAP_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,63}$")
SANDBOX_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9_.-]{0,63}$")
VERSION_RE = re.compile(r"^\d{1,6}(\.\d{1,6}){1,3}$")
DIGEST_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
TAG_RE = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9_.-]{0,127}$")
UUID_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")
ETAG_RE = re.compile(r"^(sha256:[0-9a-f]{64}|absent|dataset:[^\s]{1,200}|snapshot:[^\s]{1,264}|geen|eigenaar)$")
TS_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")
VERBODEN_SLEUTELS = ("custom_compose_config", "custom_compose_config_string", "compose")


def canoniek(value: Any) -> bytes:
    """Canonieke JSON (§8): sleutels gesorteerd, geen spaties, UTF-8; kommagetallen zijn verboden."""

    def check(v: Any) -> None:
        if isinstance(v, float):
            raise ValueError("kommagetallen zijn niet toegestaan in ondertekende gegevens")
        if isinstance(v, Mapping):
            for k, item in v.items():
                if not isinstance(k, str):
                    raise ValueError("alleen tekstsleutels")
                check(item)
        elif isinstance(v, list | tuple):
            for item in v:
                check(item)

    check(value)
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False).encode()


def sha(value: Any) -> str:
    return "sha256:" + hashlib.sha256(canoniek(value)).hexdigest()


def b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def iso(dt: datetime) -> str:
    return dt.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_ts(value: Any) -> datetime | None:
    if not isinstance(value, str) or not TS_RE.fullmatch(value):
        return None
    try:
        return datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=UTC)
    except ValueError:
        return None


POLICY_HASH = sha({"schema": "sim-policy/1", "acties": {k: list(v) for k, v in ACTIES.items()},
                   "kritiek": sorted(KRITIEKE_APPS), "sandbox": SANDBOX_DATASET})


# ------------------------------------------------------------------ resource-ETag (readback)
def _repo(ref: str) -> str:
    name = ref.split("@", 1)[0]
    last = name.rsplit("/", 1)[-1]
    return name.rsplit(":", 1)[0] if ":" in last else name


def app_beeld(app: Mapping[str, Any], images: Iterable[Mapping[str, Any]] = ()) -> dict[str, Any]:
    """Wat van een app meetelt in de ETag, uit ruwe readback (``app.query`` + ``app.image.query``): naam, staat,
    catalogusapp, trein, versie, ``custom_app``, images en digests. De app-configuratie telt niet mee (kan geheimen
    bevatten)."""
    wl_raw, meta_raw = app.get("active_workloads"), app.get("metadata")
    wl: Mapping[str, Any] = wl_raw if isinstance(wl_raw, Mapping) else {}
    meta: Mapping[str, Any] = meta_raw if isinstance(meta_raw, Mapping) else {}
    refs = sorted({str(i) for i in (wl.get("images") or []) if i}
                  | {str(c["image"]) for c in (wl.get("container_details") or [])
                     if isinstance(c, Mapping) and c.get("image")})
    digests: set[str] = {f"{_repo(r)}@{r.split('@', 1)[1]}" for r in refs if "@sha256:" in r}
    for img in images:
        if isinstance(img, Mapping) and {str(t) for t in img.get("repo_tags") or []} & set(refs):
            digests.update(str(d) for d in img.get("repo_digests") or [] if d)
    return {"naam": str(app.get("name") or app.get("id") or ""), "staat": str(app.get("state") or ""),
            "catalogus_app": str(meta.get("name") or ""), "trein": str(meta.get("train") or ""),
            "versie": str(app.get("version") or ""), "custom_app": bool(app.get("custom_app")),
            "images": refs, "digests": sorted(digests)}


def app_etag(app: Mapping[str, Any] | None, images: Iterable[Mapping[str, Any]] = ()) -> str:
    """ETag van een app: ``absent`` of de hash over :func:`app_beeld`."""
    return "absent" if app is None else sha(app_beeld(app, images))


def doel_etag(doel: str, target: Mapping[str, Any]) -> str | None:
    """ETag die zonder readback vaststaat (dataset, snapshot, geen); ``None`` voor een app (readback nodig)."""
    if doel == "dataset":
        return f"dataset:{target.get('dataset')}"
    if doel == "snapshot":
        return f"snapshot:{target.get('snapshot')}"
    if doel == "geen":
        return "geen"
    return None


# ------------------------------------------------------------------ beleid
def classificeer(action_id: str, target: Mapping[str, Any], kritiek: Iterable[str] = KRITIEKE_APPS
                 ) -> tuple[str, list[str]] | None:
    """Risicoklasse volgens de contracttabel; ``None`` = niet op de allowlist. ``risk_claim`` telt nooit mee."""
    actie = ACTIES.get(action_id)
    if actie is None:
        return None
    klasse, klasse_kritiek, _doel, _backup = actie
    regels = [f"ACT:{action_id}"]
    if klasse_kritiek and str(target.get("app_id") or "") in set(kritiek):
        return klasse_kritiek, [*regels, "APP:kritiek"]
    return klasse, regels


def action_scope(proposal: Mapping[str, Any]) -> dict[str, Any]:
    return {"action_id": proposal["action_id"], "target": dict(proposal["target"]),
            "parameters_sha256": sha(dict(proposal["parameters"]))}


def preview_hash(proposal: Mapping[str, Any]) -> str:
    return sha({"action_id": proposal["action_id"], "target": proposal["target"],
                "parameters": proposal["parameters"], "expected_version": proposal["expected_version"]})


# ------------------------------------------------------------------ vorm en parametergrenzen
def _hostpad_fout(path: Any) -> str | None:
    if not isinstance(path, str) or not path.startswith("/") or "//" in path or path != path.rstrip("/"):
        return "het hostpad moet absoluut zijn"
    parts = path.split("/")[1:]
    if any(p in ("", ".", "..") for p in parts) or "docker.sock" in path:
        return "ongeldig hostpad of Docker-socket"
    if parts[0] != "mnt" or len(parts) < 3 or parts[1].startswith(".") or parts[2].startswith("."):
        return "alleen datasets onder /mnt/<pool>/<dataset>"
    return None


def _image_fouten(img: Any, waar: str, digest: Any = None) -> list[str]:
    if not isinstance(img, Mapping) or set(img) - {"repository", "tag", "pull_policy"}:
        return [f"{waar}: verwacht {{repository, tag}}"]
    versie, sep, dig = str(img.get("tag") or "").partition("@")
    out = []
    if versie.lower() == "latest" or not TAG_RE.fullmatch(versie):
        out.append(f"{waar}.tag: geen latest, een exacte versie is verplicht")
    if sep and not DIGEST_RE.fullmatch(dig):
        out.append(f"{waar}.tag: digest moet sha256:<64 hex> zijn")
    if not sep and not (isinstance(digest, str) and DIGEST_RE.fullmatch(digest)):
        out.append(f"{waar}.tag: digest ontbreekt")
    return out


def _loop(value: Any, pad: str = "") -> Iterable[tuple[str, Any, Any]]:
    if isinstance(value, Mapping):
        for k, v in value.items():
            p = f"{pad}.{k}" if pad else str(k)
            yield p, k, v
            yield from _loop(v, p)
    elif isinstance(value, list):
        for i, v in enumerate(value):
            yield from _loop(v, f"{pad}[{i}]")


def parameter_fouten(action_id: str, target: Any, params: Any) -> list[str]:
    """Doel- en parameterschema met harde grenzen (beknopte eigen versie van de regels van de Hub)."""
    actie = ACTIES.get(action_id)
    if actie is None:
        return ["actie staat niet op de allowlist"]
    if not isinstance(target, Mapping) or not isinstance(params, Mapping):
        return ["target en parameters moeten objecten zijn"]
    doel = actie[2]
    out: list[str] = []
    if target.get("host_id") not in (None, "nas-primary"):
        out.append("target.host_id: alleen nas-primary")
    if doel in ("app", "app_nieuw"):
        if set(target) - {"host_id", "app_id"} or not APP_NAME_RE.fullmatch(str(target.get("app_id") or "")):
            out.append("target: verwacht {app_id}")
    elif doel == "dataset":
        if set(target) - {"host_id", "dataset"} or not DATASET_RE.fullmatch(str(target.get("dataset") or "")):
            out.append("target: verwacht {dataset}")
    elif doel == "snapshot":
        ds, sep, naam = str(target.get("snapshot") or "").partition("@")
        if set(target) - {"host_id", "snapshot"} or not sep or not DATASET_RE.fullmatch(ds) \
                or not SNAP_NAME_RE.fullmatch(naam):
            out.append("target: verwacht {snapshot: <dataset>@<naam>}")
    elif set(target) - {"host_id"}:
        out.append("target: geen doel verwacht")
    if action_id in ("app.restart", "app.stop", "app.start", "app.uninstall"):
        if params:
            out.append("parameters: leeg verwacht")
    elif action_id == "zfs.snapshot.create":
        if set(params) - {"naam", "recursief"} or not SNAP_NAME_RE.fullmatch(str(params.get("naam") or "")):
            out.append("parameters: verwacht {naam, recursief?}")
    elif action_id == "zfs.snapshot.clone_to_sandbox":
        pool = str(target.get("snapshot") or "").split("/", 1)[0].split("@", 1)[0]
        delen = str(params.get("doel_dataset") or "").split("/")
        if set(params) != {"doel_dataset"} or len(delen) != 3 or delen[0] != pool or delen[1] != SANDBOX_DATASET \
                or not SANDBOX_NAME_RE.fullmatch(delen[2]):
            out.append(f"parameters.doel_dataset: alleen <pool van de snapshot>/{SANDBOX_DATASET}/<naam>")
    elif action_id == "notification.send":
        if set(params) - {"titel", "tekst", "prioriteit"} or not params.get("titel") or not params.get("tekst"):
            out.append("parameters: verwacht {titel, tekst, prioriteit?}")
    elif action_id == "app.update":
        if set(params) - {"image", "catalog_version", "image_digest", "manifest_sha256"} \
                or ("image" in params) == ("catalog_version" in params):
            out.append("parameters: precies één van image of catalog_version")
        if "image" in params:
            out += _image_fouten(params["image"], "parameters.image", params.get("image_digest"))
        if "catalog_version" in params and not VERSION_RE.fullmatch(str(params["catalog_version"])):
            out.append("parameters.catalog_version: exacte versie")
    elif action_id == "app.rollback":
        if set(params) - {"herstel_van", "catalog_version"} \
                or ("herstel_van" in params) == ("catalog_version" in params):
            out.append("parameters: precies één van herstel_van of catalog_version")
        if "herstel_van" in params and not UUID_RE.fullmatch(str(params["herstel_van"])):
            out.append("parameters.herstel_van: UUID van een eerdere uitvoering")
        if "catalog_version" in params and not VERSION_RE.fullmatch(str(params["catalog_version"])):
            out.append("parameters.catalog_version: exacte versie")
    elif action_id == "app.install":
        out += _install_fouten(params, target)
    return out


def _install_fouten(params: Mapping[str, Any], target: Mapping[str, Any]) -> list[str]:
    out: list[str] = []
    if set(params) - {"app_create", "manifest", "appstore_preview_hash"}:
        out.append("parameters: onbekend veld")
    ac, manifest = params.get("app_create"), params.get("manifest")
    if not isinstance(ac, Mapping) or not isinstance(manifest, Mapping) or not manifest.get("app_id"):
        return [*out, "parameters: app_create en manifest{app_id} zijn verplicht"]
    if set(ac) - {"app_name", "custom_app", "catalog_app", "train", "version", "values"}:
        out.append("parameters.app_create: onbekend veld")
    if ac.get("custom_app") not in (None, False):
        out.append("parameters.app_create.custom_app: compose-app (Install via YAML) is nooit toegestaan")
    if ac.get("app_name") != target.get("app_id") or not APP_NAME_RE.fullmatch(str(ac.get("app_name") or "")):
        out.append("parameters.app_create.app_name: moet gelijk zijn aan target.app_id")
    if not VERSION_RE.fullmatch(str(ac.get("version") or "")):
        out.append("parameters.app_create.version: een exacte versie (nooit latest)")
    values = ac.get("values")
    if not isinstance(values, Mapping):
        return [*out, "parameters.app_create.values: verwacht een object"]
    for pad, sleutel, waarde in _loop(ac, "parameters.app_create"):
        if sleutel in VERBODEN_SLEUTELS:
            out.append(f"{pad}: compose/YAML-installatie is nooit toegestaan")
        elif sleutel == "privileged" and waarde is True:
            out.append(f"{pad}: privileged is nooit toegestaan")
        elif isinstance(waarde, str) and "docker.sock" in waarde:
            out.append(f"{pad}: een Docker-socket mag nooit in een container")
        elif sleutel == "host_network" and waarde is True and not manifest.get("host_network_reason"):
            out.append(f"{pad}: host network alleen met een reden in het manifest")
    if ac.get("catalog_app") == IX_APP:
        if ac.get("train") != "stable":
            out.append("parameters.app_create.train: ix-app staat in de trein stable")
        out += _image_fouten(values.get("image"), "parameters.app_create.values.image", manifest.get("image_digest"))
        if (values.get("capabilities") or {}).get("add") or values.get("devices") or values.get("networks"):
            out.append("parameters.app_create.values: geen extra capabilities, apparaten of gedeelde netwerken")
        for i, st in enumerate(values.get("storage") or []):
            if not isinstance(st, Mapping) or st.get("type") not in ("host_path", "ix_volume", "tmpfs"):
                out.append(f"parameters.app_create.values.storage[{i}]: alleen host_path, ix_volume of tmpfs")
            elif st.get("type") == "host_path":
                fout = _hostpad_fout((st.get("host_path_config") or {}).get("path"))
                if fout:
                    out.append(f"parameters.app_create.values.storage[{i}].host_path_config.path: {fout}")
        if not isinstance(values.get("resources"), Mapping):
            out.append("parameters.app_create.values.resources: limieten zijn verplicht")
    elif not str(manifest.get("install_reason") or "").strip():
        out.append("parameters.manifest.install_reason: een catalogusapp vraagt een reden")
    return out


def vorm_fouten(request: Any) -> list[str]:
    """Strikte vormcontrole van een ``ExecutionRequest`` (onbekende velden zijn een fout)."""
    if not isinstance(request, Mapping):
        return ["verwacht een object"]
    out = [f"onbekend veld: {k}" for k in sorted(set(request) - REQUEST_KEYS)]
    out += [f"verplicht veld ontbreekt: {k}" for k in sorted(REQUEST_KEYS - set(request))]
    p = request.get("proposal")
    if not isinstance(p, Mapping):
        return [*out, "proposal: verwacht een object"]
    out += [f"proposal: onbekend veld {k}" for k in sorted(set(p) - PROPOSAL_KEYS)]
    for k in ("idempotency_key", "action_id", "target", "parameters", "actor", "expected_version"):
        if k not in p:
            out.append(f"proposal.{k}: verplicht")
    if not UUID_RE.fullmatch(str(p.get("idempotency_key") or "")):
        out.append("proposal.idempotency_key: verwacht een UUID")
    if not ETAG_RE.fullmatch(str(p.get("expected_version") or "")):
        out.append("proposal.expected_version: ongeldige versie/ETag")
    actor = p.get("actor")
    if not isinstance(actor, Mapping) or actor.get("type") not in ACTOR_TYPES or not isinstance(actor.get("id"), str):
        out.append("proposal.actor: verwacht {type, id}")
    a = request.get("approval")
    if a is not None:
        if not isinstance(a, Mapping) or set(a) != APPROVAL_KEYS:
            out.append("approval: verwacht precies de velden van contract §8 (of null)")
        elif parse_ts(a.get("approved_at")) is None or parse_ts(a.get("expires_at")) is None:
            out.append("approval: tijden als YYYY-MM-DDTHH:MM:SSZ")
    d = request.get("policy_decision")
    if not isinstance(d, Mapping) or set(d) != {"risk_class", "rule_ids", "policy_hash"} \
            or d.get("risk_class") not in RISK_ORDER:
        out.append("policy_decision: verwacht precies {risk_class, rule_ids, policy_hash}")
    for k in ("issued_at", "expires_at"):
        if parse_ts(request.get(k)) is None:
            out.append(f"{k}: verwacht YYYY-MM-DDTHH:MM:SSZ")
    if not UUID_RE.fullmatch(str(request.get("nonce") or "")):
        out.append("nonce: verwacht een UUID")
    return out


# ------------------------------------------------------------------ handtekening
def onderteken(request: Mapping[str, Any], key: Ed25519PrivateKey) -> dict[str, Any]:
    body = {k: v for k, v in request.items() if k != "signature"}
    return {**body, "signature": b64url(key.sign(canoniek(body)))}


def handtekening_geldig(request: Mapping[str, Any], keys: Iterable[Ed25519PublicKey]) -> bool:
    sig = request.get("signature")
    if not isinstance(sig, str) or not sig:
        return False
    try:
        raw = base64.urlsafe_b64decode(sig + "=" * (-len(sig) % 4))
        message = canoniek({k: v for k, v in request.items() if k != "signature"})
    except (ValueError, TypeError):
        return False
    for key in keys:
        try:
            key.verify(raw, message)
            return True
        except InvalidSignature:
            continue
    return False
