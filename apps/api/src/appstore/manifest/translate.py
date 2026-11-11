"""Vertaling van een gevalideerd manifest naar exacte TrueNAS ``app.create``-waarden.

* ``custom_app`` → standaard Custom App = catalogusapp ``ix-app`` (``custom_app=false``) met getypte formulierwaarden;
* ``catalog_app`` → officiële catalogusapp met vaste versie en ``values``.

Er bestaat géén string-templating: parameters worden als getypte waarde op hun plek gezet (``{"param": x}`` →
waarde), gevalideerd per type. Ontbrekende of onbekende gegevens (TrueNAS-versie D01, ix-app-versie, digest,
render-node D08) leveren een blokkerende bevinding op, nooit een aangenomen waarde. Contract 1.2: poorten komen
alleen uit ``network.ports``, de tag wordt ``<tag>@<digest>`` met een verplichte digest-readback na installatie, en
een vastgezette versie die niet meer in de gemeten catalogus staat geeft ``version_unavailable``. Deze module voert
niets uit.
"""

from __future__ import annotations

import re
import zoneinfo
from typing import Annotated, Any, Literal
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field

from .canonical import sha256_digest
from .findings import Finding, Severity, error, warning
from .model import CustomApp, Manifest, Parameter, ParamRef
from .rules import _INTERPOLATION, _PLACEHOLDER, catalog_value_findings, host_path_problem, is_own_image
from .truenas import (
    IX_APP_CATALOG_APP,
    IX_APP_MAX_EXCLUSIVE,
    IX_APP_MIN_TRUENAS,
    IX_APP_MIN_VERSION,
    IX_APP_TRAIN,
    IX_APP_VERIFIED_VERSIONS,
    LABEL_PREFIX,
    AppCreatePayload,
    IxAppValues,
    IxCapabilities,
    IxDevice,
    IxEnv,
    IxGpus,
    IxHostPathConfig,
    IxImage,
    IxLabel,
    IxLimits,
    IxPort,
    IxPortal,
    IxResources,
    IxRunAs,
    IxStorage,
    IxTmpfsConfig,
    IxVolumeConfig,
    parse_version,
)

_DATASET = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]*(/[A-Za-z0-9_.:-]+)+$")
_DURATION = re.compile(r"^P(?=\d|T\d)(\d+D)?(T(?=\d)(\d+H)?(\d+M)?(\d+S)?)?$")
_TZ = re.compile(r"^(UTC|Etc/UTC|[A-Za-z_]+(/[A-Za-z0-9_+-]+){1,2})$")
_VAULT_REF = re.compile(r"^vault://[a-z0-9_-]{1,64}/[a-z0-9_.-]{1,128}$")
#: Alle stuurtekens, ook tab/newline/CR: een parameterwaarde is altijd één regel.
_CONTROL = re.compile(r"[\x00-\x1f\x7f\u0085\u2028\u2029]")


class Target(BaseModel):
    """Doelsysteem zoals gemeten (TrueNAS-API-readback) — nooit aangenomen."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    truenas_version: str | None = None
    ix_app_version: str | None = None
    #: Versies van de catalogusapp in de trein zoals gemeten (``catalog.get_app_details``); bij ``custom_app`` die van
    #: ``ix-app``. ``None`` = niet gemeten (contract 1.2 §2.2: versies kunnen uit de catalogus verdwijnen).
    available_versions: list[Annotated[str, Field(max_length=64)]] | None = Field(default=None, max_length=500)
    #: D08: is via de TrueNAS-API een Intel-render-node gemeten? ``None`` = niet gemeten.
    gpu_render_node: Literal["measured", "absent"] | None = None


class DigestReadback(BaseModel):
    """Verplichte controle na installatie/update (contract 1.2 §2.2): de executor leest de digest terug."""

    model_config = ConfigDict(frozen=True)

    method: str = "app.image.query"
    field: str = "repo_digests"
    repository: str
    expected_digest: str
    form_tag: str
    fallback_tag: str
    on_mismatch: str = "stop_en_incident"
    note_nl: str


class CompatibilityResult(BaseModel):
    model_config = ConfigDict(frozen=True)

    status: str  # ok | unknown | incompatible
    truenas_version: str | None
    ix_app_version: str | None
    reasons_nl: list[str]


class TranslationResult(BaseModel):
    model_config = ConfigDict(frozen=True)

    ready_for_install: bool
    method: str = "app.create"
    payload: AppCreatePayload | None
    preview_hash: str | None
    compatibility: CompatibilityResult
    digest_readback: DigestReadback | None = None
    findings: list[Finding]

    def blocking(self) -> list[Finding]:
        return [f for f in self.findings if f.severity is Severity.ERROR]


# ---------------------------------------------------------------------------------------------- parameters
def _check_value(param: Parameter, value: Any) -> str | None:
    """Controleer één parameterwaarde; geeft een Nederlandse fout of ``None``."""
    t = param.type
    if t == "boolean":
        return None if isinstance(value, bool) else "verwacht ja/nee (boolean)"
    if t in ("integer", "port"):
        if isinstance(value, bool) or not isinstance(value, int):
            return "verwacht een geheel getal"
        if t == "port" and not 1 <= value <= 65535:
            return "poort moet tussen 1 en 65535 liggen"
        return None
    if not isinstance(value, str):
        return "verwacht tekst"
    if _CONTROL.search(value) or len(value) > 4096:
        return "bevat stuurtekens of is te lang"
    if _PLACEHOLDER.search(value):
        return "bevat een plaatshouder in plaats van een echte waarde"
    if _INTERPOLATION.search(value):
        return "bevat variabele-interpolatie ($VAR, ${…}, $(…), $$)"
    if t == "path":
        problem = host_path_problem(value)
        return problem[1] if problem else None
    if t == "dataset":
        return None if _DATASET.match(value) else "verwacht een datasetnaam als <pool>/<dataset>"
    if t == "url":
        parts = urlsplit(value)
        if parts.scheme not in ("http", "https") or not parts.hostname:
            return "verwacht een http(s)-adres"
        if parts.username or parts.password:
            return "adres mag geen gebruikersnaam of wachtwoord bevatten"
        return None
    if t == "timezone":
        if not _TZ.match(value):
            return "onbekende tijdzone"
        try:
            zoneinfo.ZoneInfo(value)
        except (zoneinfo.ZoneInfoNotFoundError, ValueError):
            return "onbekende tijdzone"
        return None
    if t == "duration":
        return None if _DURATION.match(value) else "verwacht een ISO-8601-duur (bv. PT4H)"
    if t == "secret_ref":
        return None if _VAULT_REF.match(value) else "verwacht een vault-verwijzing vault://<scope>/<naam>"
    if t == "gpu":
        return None if value in ("none", "intel_render") else "verwacht 'none' of 'intel_render'"
    return None


class _Resolver:
    def __init__(self, manifest: Manifest, supplied: dict[str, Any]) -> None:
        self.m = manifest
        self.supplied = supplied
        self.params = {p.name: p for p in manifest.parameters}
        self.findings: list[Finding] = []
        for name in supplied:
            if name not in self.params:
                self.findings.append(
                    error(
                        "parameter_value_unknown",
                        f"parameters.{name}",
                        f"Waarde opgegeven voor onbekende parameter {name!r}.",
                    )
                )

    def gpu(self, ref: ParamRef, path: str) -> Any:
        """GPU-parameter (contract 1.2 §9): ontbreekt de waarde, dan ``none`` — nooit aangenomen dat er een GPU is."""
        if ref.param not in self.supplied:
            return "none"
        return self.get(ref, path)

    def get(self, ref: ParamRef, path: str) -> Any:
        param = self.params.get(ref.param)
        if param is None:  # door de validator al uitgesloten
            self.findings.append(error("unknown_parameter", path, f"Onbekende parameter {ref.param!r}."))
            return None
        if param.source in ("generated", "secret") or param.type == "secret_ref":
            self.findings.append(
                error(
                    "parameter_value_not_allowed",
                    path,
                    f"Parameter {param.name!r} is een geheim; geheimen gaan nooit via app.create-waarden van deze "
                    "catalogus. Invullen vanuit de vault door de executor is nog niet beschikbaar (capability_unavailable).",
                )
            )
            return None
        if ref.param in self.supplied:
            value = self.supplied[ref.param]
        elif param.source == "default" and param.default is not None:
            value = param.default
        else:
            where = {"wizard": "de onboarding-wizard", "dataset_picker": "de datasetkiezer"}.get(
                param.source, param.source
            )
            self.findings.append(
                error(
                    "parameter_value_missing",
                    path,
                    f"Waarde voor {param.name!r} ontbreekt; die komt uit {where} en wordt nooit aangenomen.",
                )
            )
            return None
        if problem := _check_value(param, value):
            self.findings.append(error("parameter_value_invalid", path, f"Waarde voor {param.name!r}: {problem}."))
            return None
        return value


# ---------------------------------------------------------------------------------------------- compatibiliteit
def check_compatibility(manifest: Manifest, target: Target) -> tuple[CompatibilityResult, list[Finding]]:
    findings: list[Finding] = []
    reasons: list[str] = []
    status = "ok"
    tn = parse_version(target.truenas_version)
    if target.truenas_version is None:
        status = "unknown"
        reasons.append("TrueNAS-versie onbekend (D01): eerst meten via de TrueNAS-API (system.version).")
        findings.append(error("truenas_version_unknown", "target.truenas_version", reasons[-1]))
    elif tn is None:
        status = "unknown"
        reasons.append(f"TrueNAS-versie {target.truenas_version!r} heeft een onbekend formaat.")
        findings.append(error("truenas_version_invalid", "target.truenas_version", reasons[-1]))
    else:
        minimums = [IX_APP_MIN_TRUENAS] if manifest.install.method == "custom_app" else []
        if manifest.compatibility:
            minimums.append(manifest.compatibility.truenas_min_version)
        for minimum in minimums:
            mv = parse_version(minimum)
            if mv and tn < mv:
                status = "incompatible"
                reasons.append(f"TrueNAS {target.truenas_version} is ouder dan het minimum {minimum}.")
                findings.append(error("truenas_version_incompatible", "target.truenas_version", reasons[-1]))
        if manifest.compatibility and manifest.compatibility.truenas_max_version:
            mx = parse_version(manifest.compatibility.truenas_max_version)
            if mx and tn[: len(mx)] > mx:
                status = "incompatible"
                reasons.append(
                    f"TrueNAS {target.truenas_version} is nieuwer dan het geteste maximum "
                    f"{manifest.compatibility.truenas_max_version}."
                )
                findings.append(error("truenas_version_incompatible", "target.truenas_version", reasons[-1]))
        if manifest.compatibility is None and status == "ok":
            status = "unknown"
            reasons.append("Het manifest geeft geen TrueNAS-compatibiliteit op.")
            findings.append(warning("compatibility_missing", "compatibility", reasons[-1]))
    if manifest.install.method == "custom_app":
        iv = parse_version(target.ix_app_version)
        if target.ix_app_version is None:
            status = "unknown" if status == "ok" else status
            reasons.append("Versie van de catalogusapp ix-app onbekend: eerst lezen via catalog.get_app_details.")
            findings.append(error("ix_app_version_unknown", "target.ix_app_version", reasons[-1]))
        elif iv is None or not (IX_APP_MIN_VERSION <= iv < IX_APP_MAX_EXCLUSIVE):
            status = "incompatible"
            reasons.append(
                f"ix-app {target.ix_app_version} valt buiten de ondersteunde reeks "
                f">= {'.'.join(map(str, IX_APP_MIN_VERSION))} en < {'.'.join(map(str, IX_APP_MAX_EXCLUSIVE))}."
            )
            findings.append(error("ix_app_version_unsupported", "target.ix_app_version", reasons[-1]))
        else:
            if manifest.compatibility and manifest.compatibility.ix_app_min_version:
                need = parse_version(manifest.compatibility.ix_app_min_version)
                if need and iv < need:
                    status = "incompatible"
                    reasons.append(
                        f"ix-app {target.ix_app_version} is ouder dan vereist ({manifest.compatibility.ix_app_min_version})."
                    )
                    findings.append(error("ix_app_version_unsupported", "target.ix_app_version", reasons[-1]))
            if target.ix_app_version not in IX_APP_VERIFIED_VERSIONS:
                findings.append(
                    warning(
                        "ix_app_version_unverified",
                        "target.ix_app_version",
                        f"ix-app {target.ix_app_version} is niet letterlijk nagelopen (wel: {', '.join(IX_APP_VERIFIED_VERSIONS)}); "
                        "de executor moet het waardenschema op de NAS controleren.",
                    )
                )
    status = _check_availability(manifest, target, findings, reasons, status)
    return (
        CompatibilityResult(
            status=status,
            truenas_version=target.truenas_version,
            ix_app_version=target.ix_app_version,
            reasons_nl=reasons,
        ),
        findings,
    )


def _check_availability(
    manifest: Manifest, target: Target, findings: list[Finding], reasons: list[str], status: str
) -> str:
    """Contract 1.2 §2.2: een vastgezette versie kan uit de catalogus verdwijnen → ``version_unavailable``.

    Nooit stil een nieuwere versie of ``latest`` kiezen; het voorstel is altijd een manifest-update met een nieuwe
    vaste versie plus test (catalogusapp) of het testen van de vertaling voor de nieuwe ``ix-app``-versie.
    """
    custom = manifest.install.method == "custom_app"
    pinned = target.ix_app_version if custom else (manifest.catalog.version if manifest.catalog else None)
    what = "ix-app" if custom else (manifest.catalog.app if manifest.catalog else "?")
    if target.available_versions is None:
        findings.append(
            warning(
                "version_availability_unknown",
                "target.available_versions",
                f"Niet gemeten welke versies van {what} de TrueNAS-catalogus nog heeft (catalog.get_app_details); "
                "de executor moet dat vóór uitvoering controleren.",
            )
        )
        return status
    available = set(target.available_versions)
    problem: str | None = None
    if pinned is not None and pinned not in available:
        problem = (
            f"{what} {pinned} staat niet (meer) in de gemeten catalogus; installeren zou mislukken (Version not "
            "found). Voorstel: "
            + (
                "de vertaling voor een beschikbare ix-app-versie testen."
                if custom
                else "het manifest bijwerken naar een nieuwe vaste versie en die testen."
            )
        )
    elif custom and not available & set(IX_APP_VERIFIED_VERSIONS):
        newest = max(available, key=lambda v: parse_version(v) or (0,), default="?")
        problem = (
            f"Geen van de geteste ix-app-versies ({', '.join(IX_APP_VERIFIED_VERSIONS)}) staat nog in de gemeten "
            f"catalogus. Voorstel: de vertaling voor ix-app {newest} testen."
        )
    if problem:
        reasons.append(problem)
        findings.append(error("version_unavailable", "target.available_versions", problem))
        return "incompatible"
    return status


# ---------------------------------------------------------------------------------------------- vertaling
def _custom_app_values(
    m: Manifest, ca: CustomApp, r: _Resolver, manifest_sha: str, target: Target
) -> IxAppValues | None:
    assert m.image is not None
    decls = {s.name: s for s in m.storage}
    # digest vastgezet in het tagveld (ADR-0004); zonder digest blokkeert translate() de installatie
    tag = f"{m.image.tag}@{m.image.digest}" if m.image.digest else m.image.tag
    ports: list[IxPort] = []
    host_port_by_name: dict[str, int] = {}
    for idx, p in enumerate(m.network.ports):  # contract 1.2 §9: de enige bron voor de formulierpoorten
        host = p.host_port if isinstance(p.host_port, int) else r.get(p.host_port, f"network.ports[{idx}].host_port")
        if host is None:
            continue
        host_port_by_name[p.name] = int(host)
        # same_as_host is alleen voor catalogusapps; de validator weigert het bij custom_app (same_as_host_not_allowed)
        container = int(host) if p.container_port == "same_as_host" else p.container_port
        ports.append(IxPort(port_number=int(host), container_port=container, protocol=p.protocol))
    storage: list[IxStorage] = []
    for idx, sb in enumerate(ca.storage):
        decl = decls[sb.name]
        if sb.type == "host_path":
            assert isinstance(sb.host_path, ParamRef)  # letterlijke paden weigert de validator
            path = r.get(sb.host_path, f"custom_app.storage[{idx}].host_path")
            if path is None:
                continue
            storage.append(
                IxStorage(
                    type="host_path",
                    read_only=sb.read_only,
                    mount_path=decl.mount,
                    host_path_config=IxHostPathConfig(path=path),
                )
            )
        elif sb.type == "ix_volume":
            assert sb.dataset_name is not None
            storage.append(
                IxStorage(
                    type="ix_volume",
                    read_only=sb.read_only,
                    mount_path=decl.mount,
                    ix_volume_config=IxVolumeConfig(dataset_name=sb.dataset_name),
                )
            )
        else:
            assert sb.tmpfs_size_mib is not None
            storage.append(
                IxStorage(
                    type="tmpfs",
                    read_only=sb.read_only,
                    mount_path=decl.mount,
                    tmpfs_config=IxTmpfsConfig(size=sb.tmpfs_size_mib),
                )
            )
    envs: list[IxEnv] = []
    for idx, env in enumerate(ca.env):
        if env.value is not None:
            envs.append(IxEnv(name=env.name, value=env.value))
        elif env.value_from is not None:
            value = r.get(env.value_from, f"custom_app.env[{idx}].value_from")
            if value is not None:
                envs.append(IxEnv(name=env.name, value=str(value).lower() if isinstance(value, bool) else str(value)))
    tz = r.get(ca.timezone, "custom_app.timezone") if isinstance(ca.timezone, ParamRef) else (ca.timezone or "Etc/UTC")
    portals: list[IxPortal] = []
    if ca.portal and ca.portal.port in host_port_by_name:
        portals.append(
            IxPortal(
                name=ca.portal.name,
                scheme=ca.portal.scheme,
                port=host_port_by_name[ca.portal.port],
                path=ca.portal.path,
            )
        )
    limits = None
    if ca.resources.cpus is not None and ca.resources.memory_mib is not None:
        limits = IxLimits(cpus=ca.resources.cpus, memory=ca.resources.memory_mib)
    elif (ca.resources.cpus is None) != (ca.resources.memory_mib is None):
        r.findings.append(
            warning(
                "invalid_value",
                "custom_app.resources",
                "Het formulier kent alleen limieten voor CPU én geheugen samen; zonder beide geen limiet.",
            )
        )
    gpu = r.gpu(ca.gpu, "custom_app.gpu") if isinstance(ca.gpu, ParamRef) else ca.gpu
    if gpu == "intel_render":
        if m.privileges.gpu != "intel_render":  # door de validator al uitgesloten; verdediging in de diepte
            r.findings.append(error("gpu_exceeds_privileges", "custom_app.gpu", "GPU gevraagd boven privileges.gpu."))
        elif target.gpu_render_node != "measured":
            r.findings.append(
                error(
                    "gpu_not_measured",
                    "target.gpu_render_node",
                    "GPU (intel_render) gevraagd, maar D08 heeft "
                    + (
                        "gemeten dat er geen render-node is."
                        if target.gpu_render_node == "absent"
                        else "nog niets gemeten."
                    )
                    + " Kies 'none' of meet eerst de render-node via de TrueNAS-API.",
                )
            )
        else:
            r.findings.append(
                warning(
                    "gpu_all_render_devices",
                    "custom_app.gpu",
                    "Het formulier kan niet per GPU kiezen: use_all_gpus geeft de app elke niet-NVIDIA-render-GPU van "
                    "de NAS (NIET GEVALIDEERD op een echte NAS).",
                )
            )
    if any(f.severity is Severity.ERROR for f in r.findings) or tz is None:
        return None
    labels = [
        IxLabel(key=f"{LABEL_PREFIX}.app_id", value=m.app_id),
        IxLabel(key=f"{LABEL_PREFIX}.manifest_sha256", value=manifest_sha),
        IxLabel(key=f"{LABEL_PREFIX}.managed_by", value="appstore-en-fork"),
    ]
    return IxAppValues(
        notes=(
            f"Geïnstalleerd via het TrueNAS Home Platform (appstore-en-fork), manifest {m.app_id} {m.version}.\n"
            f"Manifest-hash: `{manifest_sha}`. Wijzig deze app bij voorkeur via de platform-UI."
        ),
        image=IxImage(repository=m.image.repository, tag=tag, pull_policy="missing"),
        TZ=str(tz),
        envs=envs,
        restart_policy=ca.restart_policy,
        max_retry_count=0 if ca.restart_policy == "on-failure" else None,
        devices=[
            IxDevice(host_device=d.host_device, container_device=d.container_device) for d in m.privileges.devices
        ],
        capabilities=IxCapabilities(add=[c.upper().removeprefix("CAP_") for c in m.privileges.cap_add]),
        run_as_custom_user=ca.run_as.mode == "custom",
        run_as=IxRunAs(user=ca.run_as.user, group=ca.run_as.group)
        if ca.run_as.mode == "custom" and ca.run_as.user and ca.run_as.group
        else None,
        host_network=m.network.host_network,
        ports=[] if m.network.host_network else ports,
        portals=portals,
        storage=storage,
        labels=labels,
        resources=IxResources(
            enable_resource_limits=limits is not None, limits=limits, gpus=IxGpus(use_all_gpus=gpu == "intel_render")
        ),
    )


def _substitute(value: Any, r: _Resolver, path: str) -> Any:
    if isinstance(value, dict):
        if set(value) == {"param"} and isinstance(value["param"], str):
            return r.get(ParamRef(param=value["param"]), path)
        return {k: _substitute(v, r, f"{path}.{k}") for k, v in value.items()}
    if isinstance(value, list):
        return [_substitute(v, r, f"{path}[{i}]") for i, v in enumerate(value)]
    return value


def translate(
    manifest: Manifest,
    parameters: dict[str, Any] | None = None,
    target: Target | None = None,
    app_name: str | None = None,
    manifest_sha256: str | None = None,
) -> TranslationResult:
    """Vertaal een **gevalideerd** manifest. Geeft altijd een resultaat; ``payload`` is ``None`` bij blokkades."""
    target = target or Target()
    manifest_sha = manifest_sha256 or sha256_digest(manifest.model_dump(mode="json", exclude_none=False))
    compat, findings = check_compatibility(manifest, target)
    resolver = _Resolver(manifest, dict(parameters or {}))
    name = app_name or manifest.app_id
    payload: AppCreatePayload | None = None
    readback: DigestReadback | None = None
    if manifest.install.method == "custom_app":
        assert manifest.custom_app is not None and manifest.image is not None
        if not manifest.image.digest:
            findings.append(
                error(
                    "digest_not_pinned",
                    "image.digest",
                    "Zonder digest zou TrueNAS een veranderlijke referentie ophalen; installeren is pas gereed als de "
                    + (
                        "release-CI de digest heeft ingevuld."
                        if is_own_image(manifest.image.repository)
                        else "digest bekend is."
                    ),
                )
            )
        values = _custom_app_values(manifest, manifest.custom_app, resolver, manifest_sha, target)
        if manifest.image.digest:
            readback = DigestReadback(
                repository=manifest.image.repository,
                expected_digest=manifest.image.digest,
                form_tag=f"{manifest.image.tag}@{manifest.image.digest}",
                fallback_tag=manifest.image.tag,
                note_nl=(
                    "Na installatie/update leest de executor de digest terug (app.image.query, repo_digests) en stopt "
                    "met een incident bij afwijking. Accepteert het formulier de tag met '@sha256:' niet, dan alleen de "
                    "exacte versie invullen; de readback is dan de enige controle (NIET GEVALIDEERD op een echte NAS)."
                ),
            )
        if values is not None and target.ix_app_version is not None:
            payload = AppCreatePayload(
                app_name=name,
                catalog_app=IX_APP_CATALOG_APP,
                train=IX_APP_TRAIN,
                version=target.ix_app_version,
                values=values.model_dump(mode="json", exclude_none=True),
            )
    else:
        assert manifest.catalog is not None and manifest.values is not None
        values_any = _substitute(manifest.values, resolver, "values")
        # verdediging in de diepte: de ingevulde waarden opnieuw controleren (een parameter smokkelt niets binnen)
        resolver.findings.extend(
            catalog_value_findings(values_any, host_network_reason=manifest.network.host_network_reason, resolved=True)
        )
        if not any(f.severity is Severity.ERROR for f in resolver.findings):
            payload = AppCreatePayload(
                app_name=name,
                catalog_app=manifest.catalog.app,
                train=manifest.catalog.train,
                version=manifest.catalog.version,
                values=values_any,
            )
    findings = findings + resolver.findings
    blocking = any(f.severity is Severity.ERROR for f in findings)
    preview_hash = sha256_digest(payload.model_dump(mode="json")) if payload is not None else None
    return TranslationResult(
        ready_for_install=payload is not None and not blocking and compat.status == "ok",
        payload=payload,
        preview_hash=preview_hash,
        compatibility=compat,
        digest_readback=readback,
        findings=sorted(findings, key=lambda f: (f.severity is not Severity.ERROR, f.path, f.code)),
    )
