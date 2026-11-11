"""Vertaling van een gevalideerd manifest naar exacte TrueNAS ``app.create``-waarden.

* ``custom_app`` → standaard Custom App = catalogusapp ``ix-app`` (``custom_app=false``) met getypte formulierwaarden;
* ``catalog_app`` → officiële catalogusapp met vaste versie en ``values``.

Er bestaat géén string-templating: parameters worden als getypte waarde op hun plek gezet (``{"param": x}`` →
waarde), gevalideerd per type. Ontbrekende of onbekende gegevens (TrueNAS-versie D01, ix-app-versie, digest,
render-node D08) leveren een blokkerende bevinding op, nooit een aangenomen waarde. Contract 1.2: poorten komen
alleen uit ``network.ports``, de tag wordt ``<tag>@<digest>`` met een verplichte digest-readback na installatie, en
een vastgezette versie die niet meer in de gemeten catalogus staat geeft ``version_unavailable``. Deze module voert
niets uit.

Geheimen (contract 1.3 §3.1 a, C1.3-ASF-14, ADR-0015): een geheime parameter (``source: generated``/``secret``,
``type: secret_ref`` of ``secret: true``) wordt nooit ingevuld. Op zijn plek komt alleen de verwijzing
``vault://app/<manifest.app_id>/<parameter>``; ``TranslationResult.vault_refs`` noemt elke plek, zodat de Hub en de
executor weten wat zij uit de vault invullen. Dat kan in de ``values`` van een catalogusapp en in een
omgevingsvariabele van een Custom App naar een geheim dat de Hub maakt (besluit van de gebruiker, 2026-11-11). Elke
andere vault-verwijzing in de payload wordt geweigerd; een melding noemt nooit een waarde.
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
#: Vorm van een vault-verwijzing (contract 1.3 §3.1 a; CONTRACT_1.3_CONFORMITEIT, rij C1.3-ASM-18). Gelijk aan
#: ``VAULT_REF_RE`` van de policy van de Hub (ai-server-manager ``apps/policy/src/asm_policy/params.py``).
_VAULT_REF = re.compile(r"vault://app/(?P<app_id>[a-z0-9][a-z0-9_-]{0,63})/(?P<parameter>[a-z0-9_.-]{1,128})")
#: Alles wat op een vault-verwijzing lijkt (ook met hoofdletters of midden in een tekst).
_VAULT_MARK = re.compile(r"vault://", re.IGNORECASE)
#: Alleen deze parametertypen kunnen een verwijzing (tekst) krijgen; de Hub maakt tekstgeheimen (§3.1 a).
_VAULT_REF_TYPES = ("string", "secret_ref")
#: Een sleutel met een van deze tekens maakt het pad van een verwijzing dubbelzinnig (``a.b`` naast ``a`` → ``b``).
_PATH_SEPARATORS = frozenset(".[]")
#: Sleutels waaronder nooit een verwijzing komt: daar is een waarde openbaar of komt zij in een log (image-naam bij het
#: ophalen, tijdzone, notities, labels; ADR-0015).
_PUBLIC_KEYS = frozenset({"image", "images", "tz", "notes", "labels"})
#: Plek in de payload als reeks sleutels en lijstposities, bv. ``("values", "envs", 2, "value")``.
Segments = tuple[str | int, ...]
_NO_TEXT_PLACE = (
    "een geheim kan hier niet als vault-verwijzing staan (alleen in de waarden van een catalogusapp of in een "
    "omgevingsvariabele van een Custom App); deze plek vraagt een waarde, en een geheim gaat nooit als waarde mee"
)
#: Alle stuurtekens, ook tab/newline/CR: een parameterwaarde is altijd één regel.
_CONTROL = re.compile(r"[\x00-\x1f\x7f\u0085\u2028\u2029]")


def vault_ref(app_id: str, parameter: str) -> str:
    """De verwijzing naar een geheim in de vault van de Hub: ``vault://app/<app_id>/<parameter>``."""
    return f"vault://app/{app_id}/{parameter}"


def render_path(segments: Segments) -> str:
    """Pad als tekst, bv. ``values.envs[2].value``. Eenduidig voor een verwijzing: de vertaling zet die nooit onder een
    sleutel met ``.``, ``[`` of ``]``."""
    out = ""
    for s in segments:
        out += f"[{s}]" if isinstance(s, int) else (f".{s}" if out else s)
    return out


def is_secret(param: Parameter) -> bool:
    """Geheime parameter: de vertaling vult hem nooit in (contract 1.3 §3.1 a, C1.3-ASF-14)."""
    return param.source in ("generated", "secret") or param.type == "secret_ref" or param.secret


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


class VaultRef(BaseModel):
    """Plek in de payload waar de executor een geheim uit de vault van de Hub invult (C1.3-ASF-14, ADR-0015)."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    #: Pad in de payload, bv. ``values.voorbeeld.db_password`` of ``values.envs[2].value``.
    path: str
    #: Naam van de geheime parameter in het manifest.
    parameter: str
    #: De verwijzing die op die plek staat: ``vault://app/<app_id>/<parameter>``.
    ref: str


class TranslationResult(BaseModel):
    model_config = ConfigDict(frozen=True)

    ready_for_install: bool
    method: str = "app.create"
    payload: AppCreatePayload | None
    preview_hash: str | None
    compatibility: CompatibilityResult
    digest_readback: DigestReadback | None = None
    #: Elke plek in ``payload`` met een vault-verwijzing; leeg als er geen payload is.
    vault_refs: list[VaultRef] = Field(default_factory=list)
    findings: list[Finding]

    def blocking(self) -> list[Finding]:
        return [f for f in self.findings if f.severity is Severity.ERROR]


# ---------------------------------------------------------------------------------------------- parameters
def _check_value(param: Parameter, value: Any) -> str | None:
    """Controleer één parameterwaarde; geeft een Nederlandse fout of ``None`` (nooit de waarde zelf).

    Geheime parameters komen hier nooit: die krijgen alleen een vault-verwijzing (:meth:`_Resolver.secret`).
    """
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
    if _VAULT_MARK.search(value):
        return "bevat een vault-verwijzing; die zet de vertaling alleen zelf, voor een geheime parameter"
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
    if t == "gpu":
        return None if value in ("none", "intel_render") else "verwacht 'none' of 'intel_render'"
    return None


class _Resolver:
    def __init__(self, manifest: Manifest, supplied: dict[str, Any], *, trusted_app_id: bool) -> None:
        self.m = manifest
        self.supplied = supplied
        self.trusted_app_id = trusted_app_id
        self.params = {p.name: p for p in manifest.parameters}
        self.findings: list[Finding] = []
        #: Elke vault-verwijzing die deze vertaling zelf op een plek in de payload heeft gezet.
        self.refs: list[VaultRef] = []
        #: Dezelfde verwijzingen per echte plek (sleutels en lijstposities), voor de controle op ongemarkeerde tekst.
        self.ref_at: dict[Segments, str] = {}
        #: Geheime parameters die de vertaling op een plek tegenkwam (een opgegeven waarde is dan al beoordeeld).
        self.secrets_seen: set[str] = set()
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

    def get(self, ref: ParamRef, path: str, *, ref_at: Segments | None = None, hub_generated_only: bool = False) -> Any:
        """Waarde van een parameter op ``path`` (manifestpad).

        ``ref_at`` = de plek in de payload als daar een vault-verwijzing mag staan (tekst in de ``values`` van een
        catalogusapp of in een omgevingsvariabele); zonder ``ref_at`` wordt een geheime parameter geweigerd.
        ``hub_generated_only``: alleen een geheim dat de Hub maakt (``source: generated`` + ``secret: true``).
        """
        param = self.params.get(ref.param)
        if param is None:  # door de validator al uitgesloten
            self.findings.append(error("unknown_parameter", path, f"Onbekende parameter {ref.param!r}."))
            return None
        if is_secret(param):
            return self.secret(param, path, ref_at, hub_generated_only=hub_generated_only)
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

    def secret(self, param: Parameter, path: str, ref_at: Segments | None, *, hub_generated_only: bool) -> str | None:
        """C1.3-ASF-14: een geheim wordt nooit ingevuld; alleen ``vault://app/<app_id>/<parameter>`` (de vaste
        ``app_id`` uit het manifest, nooit de gekozen appnaam), en alleen als die ``app_id`` vaststaat
        (``trusted_app_id``). Een opgegeven waarde mag alleen precies die eigen verwijzing zijn; al het andere wordt
        geweigerd. De melding noemt de parameter, nooit de opgegeven waarde."""
        self.secrets_seen.add(param.name)
        own = vault_ref(self.m.app_id, param.name)
        keys = [s for s in (ref_at or ()) if isinstance(s, str)]
        reason: str | None = None
        if ref_at is None:
            reason = _NO_TEXT_PLACE
        elif param.type not in _VAULT_REF_TYPES:
            reason = f"een vault-verwijzing kan alleen bij type 'string' of 'secret_ref', niet bij {param.type!r}"
        elif hub_generated_only and not param.hub_generated_secret:
            reason = (
                "in het Custom App-formulier alleen een geheim dat de Hub maakt (source generated + secret true); "
                "elk ander geheim blijft uit het formulier"
            )
        elif any(_PATH_SEPARATORS & set(k) for k in keys):
            reason = (
                "een verwijzing staat nooit onder een sleutel met '.', '[' of ']': het pad naar de plek zou "
                "dubbelzinnig zijn en de executor kan het geheim dan in een ander veld zetten"
            )
        elif any(k.lower() in _PUBLIC_KEYS for k in keys):
            reason = (
                "een verwijzing staat nooit op een openbare plek (image, tijdzone, notities of labels): daar komt de "
                "waarde in een log of in het overzicht van TrueNAS"
            )
        elif not self.trusted_app_id:
            reason = (
                "een vault-verwijzing kan alleen als de app_id vaststaat (een geldige versie uit de catalogus); bij een "
                "los manifest kan de app_id die van een andere app zijn"
            )
        elif param.name in self.supplied:
            supplied = self.supplied[param.name]
            if not (isinstance(supplied, str) and supplied == own):
                reason = self._supplied_reason(supplied, own)
        if reason is not None or ref_at is None:  # ref_at is dan altijd al met een reden geweigerd
            self.findings.append(
                error(
                    "parameter_value_not_allowed",
                    path,
                    f"Parameter {param.name!r} is een geheim: {reason or _NO_TEXT_PLACE}.",
                )
            )
            return None
        self.refs.append(VaultRef(path=render_path(ref_at), parameter=param.name, ref=own))
        self.ref_at[ref_at] = own
        return own

    def unused_secret_values(self) -> None:
        """R-TECH-2: een opgegeven waarde voor een geheime parameter die nergens gebruikt wordt, wordt ook geweigerd
        (de eigen verwijzing mag; die zet niets in de payload)."""
        for name, supplied in self.supplied.items():
            param = self.params.get(name)
            if param is None or not is_secret(param) or name in self.secrets_seen:
                continue
            if isinstance(supplied, str) and supplied == vault_ref(self.m.app_id, name):
                continue
            self.findings.append(
                error(
                    "parameter_value_not_allowed",
                    f"parameters.{name}",
                    f"Parameter {name!r} is een geheim: een waarde voor een geheim wordt nooit meegegeven, ook niet "
                    "als het manifest de parameter niet gebruikt.",
                )
            )

    def _supplied_reason(self, supplied: Any, own: str) -> str:
        match = _VAULT_REF.fullmatch(supplied) if isinstance(supplied, str) else None
        if match is not None and match["app_id"] != self.m.app_id:
            return f"de opgegeven vault-verwijzing hoort bij een andere app; alleen {own} is toegestaan"
        if match is not None:
            return f"de opgegeven vault-verwijzing hoort bij een andere parameter; alleen {own} is toegestaan"
        return (
            "een waarde voor een geheim wordt nooit meegegeven; de vertaling zet alleen de verwijzing "
            f"{own} en de executor vult de waarde in"
        )


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
            # besluit gebruiker 2026-11-11: ook hier een vault-verwijzing, alleen naar een geheim dat de Hub maakt
            value = r.get(
                env.value_from,
                f"custom_app.env[{idx}].value_from",
                ref_at=("values", "envs", len(envs), "value"),
                hub_generated_only=True,
            )
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


def _substitute(value: Any, r: _Resolver, at: Segments) -> Any:
    """Vul de parameters in de ``values`` van een catalogusapp in; ``at`` is ook de plek in de payload."""
    if isinstance(value, dict):
        if set(value) == {"param"} and isinstance(value["param"], str):
            return r.get(ParamRef(param=value["param"]), render_path(at), ref_at=at)
        return {k: _substitute(v, r, (*at, k)) for k, v in value.items()}
    if isinstance(value, list):
        return [_substitute(v, r, (*at, i)) for i, v in enumerate(value)]
    return value


def _texts(value: Any, at: Segments) -> list[tuple[Segments, str, bool]]:
    """(plek, tekst, is_sleutel) voor elke tekstwaarde en elke tekstsleutel in de payload. Bij een sleutel is de plek
    die van het object waarin de sleutel staat."""
    if isinstance(value, dict):
        keys = [(at, k, True) for k in value if isinstance(k, str)]
        return keys + [t for k, v in value.items() for t in _texts(v, (*at, k))]
    if isinstance(value, list):
        return [t for i, v in enumerate(value) for t in _texts(v, (*at, i))]
    return [(at, value, False)] if isinstance(value, str) else []


def _unmarked_vault_refs(payload: AppCreatePayload, ref_at: dict[Segments, str]) -> list[Finding]:
    """Alleen de vertaling maakt een vault-verwijzing. Een letterlijke verwijzing in het manifest (of iets dat erop
    lijkt, ook in een sleutel) zou de Hub een geheim van een andere app laten invullen: weigeren, met het pad en zonder
    de waarde. De vergelijking gaat over de echte plek (sleutels en lijstposities), niet over het pad als tekst: een
    sleutel ``a.b`` is een andere plek dan ``a`` → ``b``."""
    return [
        error(
            "vault_ref_not_allowed",
            render_path(at),
            (
                "In een sleutel van dit object staat een vault-verwijzing"
                if is_key
                else "Hier staat een vault-verwijzing die de vertaling niet zelf heeft gemaakt"
            )
            + "; alleen een geheime parameter krijgt een verwijzing naar de vault van de Hub, en alleen voor de eigen "
            "app.",
        )
        for at, text, is_key in _texts(payload.model_dump(mode="json"), ())
        if _VAULT_MARK.search(text) and (is_key or ref_at.get(at) != text)
    ]


def translate(
    manifest: Manifest,
    parameters: dict[str, Any] | None = None,
    target: Target | None = None,
    app_name: str | None = None,
    manifest_sha256: str | None = None,
    *,
    trusted_app_id: bool = False,
) -> TranslationResult:
    """Vertaal een **gevalideerd** manifest. Geeft altijd een resultaat; ``payload`` is ``None`` bij blokkades.

    ``trusted_app_id``: de aanroeper staat in voor de ``app_id`` van het manifest (een geldige versie uit de catalogus,
    of het eigen bestand bij ``jl-manifest translate``). Alleen dan krijgt een geheime parameter een vault-verwijzing;
    standaard niet, want een los manifest kan zich de ``app_id`` van een andere app geven (ADR-0015).
    """
    target = target or Target()
    manifest_sha = manifest_sha256 or sha256_digest(manifest.model_dump(mode="json", exclude_none=False))
    compat, findings = check_compatibility(manifest, target)
    resolver = _Resolver(manifest, dict(parameters or {}), trusted_app_id=trusted_app_id)
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
        values_any = _substitute(manifest.values, resolver, ("values",))
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
    resolver.unused_secret_values()
    if any(f.severity is Severity.ERROR for f in resolver.findings):
        payload = None  # ook een geweigerde waarde voor een ongebruikte geheime parameter blokkeert de payload
    findings = findings + resolver.findings
    if payload is not None and (unmarked := _unmarked_vault_refs(payload, resolver.ref_at)):
        findings += unmarked
        payload = None  # nooit een payload met een verwijzing die niet gemarkeerd is
    blocking = any(f.severity is Severity.ERROR for f in findings)
    preview_hash = sha256_digest(payload.model_dump(mode="json")) if payload is not None else None
    return TranslationResult(
        ready_for_install=payload is not None and not blocking and compat.status == "ok",
        payload=payload,
        preview_hash=preview_hash,
        compatibility=compat,
        digest_readback=readback,
        vault_refs=list(resolver.refs) if payload is not None else [],
        findings=sorted(findings, key=lambda f: (f.severity is not Severity.ERROR, f.path, f.code)),
    )
