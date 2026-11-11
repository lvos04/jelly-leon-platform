"""Semantische veiligheidsregels van manifest v1 (platformcontract 1.2 §9, EX47).

Het JSON Schema controleert vorm en types; deze regels controleren betekenis: geen privileged, geen Docker-socket,
geen gevaarlijke hostpaden, geen ``latest``, digest bij upstream-images, geen voorbeeldwaarden als echte waarde,
volledige back-up/upgrade/risico-gegevens, GPU alleen binnen ``privileges.gpu`` met D08. Vervallen velden uit 1.1
(``network.joins_platform_network``, ``custom_app.ports``) geven tijdens de overgang ``deprecated_field``
(waarschuwing; ``--strict`` maakt er een fout van). Alle regels zijn deterministisch en werken uitsluitend op het
getypte model; er wordt niets uitgevoerd of opgehaald.
"""

from __future__ import annotations

import ipaddress
import re
import unicodedata
from collections.abc import Iterable, Iterator
from typing import Any

from .findings import Finding, Severity, error, warning
from .model import (
    Manifest,
    Parameter,
    ParamRef,
    PortBinding,
)

#: Eigen images (release-CI vult de digest). Alles daarbuiten is upstream en moet een digest hebben.
OWN_IMAGE_PREFIXES: tuple[str, ...] = ("ghcr.io/lvos04/",)

#: Tags die per definitie verschuiven.
MUTABLE_TAGS = frozenset(
    {
        "latest",
        "stable",
        "main",
        "master",
        "edge",
        "nightly",
        "dev",
        "develop",
        "beta",
        "alpha",
        "rolling",
        "lts",
        "current",
        "release",
        "head",
        "testing",
        "unstable",
        "next",
        "preview",
        "rc",
    }
)
#: Woorden die als *onderdeel* van een tag op een verschuivende tag wijzen (``alpine-latest``, ``stable-alpine``).
#: Niet: alpha/beta/rc/release/preview/next (komen ook voor in vastgezette voorversies zoals ``2.0.0-rc.1``).
MUTABLE_TAG_PARTS = frozenset(
    {
        "latest",
        "stable",
        "edge",
        "nightly",
        "main",
        "master",
        "dev",
        "develop",
        "rolling",
        "current",
        "head",
        "testing",
        "unstable",
        "lts",
    }
)
_TAG_SPLIT = re.compile(r"[._-]")
_DOTTED_VERSION = re.compile(r"\d+\.\d+\.\d+")
_IMPRECISE_TAG = re.compile(r"^v?\d+(\.\d+)?(-[a-z][a-z0-9.]*)?$")
_EXACT_VERSION = re.compile(r"^v?\d+\.\d+\.\d+([.+_-][0-9A-Za-z.+_-]+)?$")

#: Platformminimum per lifecycle-actie (contract §8: app.install/update/uninstall minimaal R2).
RISK_MINIMUM = {"install": "R2", "update": "R2", "uninstall": "R2"}
_RISK_RANK = {"R0": 0, "R1": 1, "R2": 2, "R3": 3}

#: Hostpaden die nooit gekoppeld mogen worden (exact of als ouder/kind). TrueNAS weigert er een deel zelf al
#: (truenas/apps library 2.3.18 ``validations.RESTRICTED``); wij zijn strenger.
FORBIDDEN_HOST_PATHS: tuple[str, ...] = (
    "/etc",
    "/var/run",
    "/run",
    "/root",
    "/boot",
    "/home",
    "/var/log",
    "/var/db",
    "/var/lib/docker",
    "/data",
    "/conf",
    "/audit",
    "/proc",
    "/sys",
    "/dev",
    "/usr",
    "/bin",
    "/sbin",
    "/lib",
    "/lib64",
    "/opt",
    "/srv",
    "/tmp",  # noqa: S108 - verboden hostpad, geen tijdelijk bestand
    "/var",
    "/mnt/.ix-apps",
)
#: Alleen als exact pad verboden (kinderen zijn wél toegestaan, mits via de datasetkiezer).
FORBIDDEN_EXACT_HOST_PATHS: tuple[str, ...] = ("/", "/mnt")

#: Koppelpaden in de container die nooit overschreven mogen worden.
FORBIDDEN_MOUNT_PATHS: tuple[str, ...] = (
    "/",
    "/proc",
    "/sys",
    "/dev",
    "/etc",
    "/run",
    "/var/run",
    "/boot",
    "/usr",
    "/bin",
    "/sbin",
    "/lib",
    "/lib64",
)
_SOCKET_MARKERS = ("docker.sock", "containerd.sock", "podman.sock", "crio.sock", "dockershim.sock", "buildkitd.sock")

#: Linux-capabilities (man 7 capabilities). ``ALL`` is nooit toegestaan.
KNOWN_CAPABILITIES = frozenset(
    {
        "AUDIT_CONTROL",
        "AUDIT_READ",
        "AUDIT_WRITE",
        "BLOCK_SUSPEND",
        "BPF",
        "CHECKPOINT_RESTORE",
        "CHOWN",
        "DAC_OVERRIDE",
        "DAC_READ_SEARCH",
        "FOWNER",
        "FSETID",
        "IPC_LOCK",
        "IPC_OWNER",
        "KILL",
        "LEASE",
        "LINUX_IMMUTABLE",
        "MAC_ADMIN",
        "MAC_OVERRIDE",
        "MKNOD",
        "NET_ADMIN",
        "NET_BIND_SERVICE",
        "NET_BROADCAST",
        "NET_RAW",
        "PERFMON",
        "SETFCAP",
        "SETGID",
        "SETPCAP",
        "SETUID",
        "SYSLOG",
        "SYS_ADMIN",
        "SYS_BOOT",
        "SYS_CHROOT",
        "SYS_MODULE",
        "SYS_NICE",
        "SYS_PACCT",
        "SYS_PTRACE",
        "SYS_RAWIO",
        "SYS_RESOURCE",
        "SYS_TIME",
        "SYS_TTY_CONFIG",
        "WAKE_ALARM",
    }
)
#: Capabilities die gelijkstaan aan (bijna) root op de host: altijd geweigerd, ook mét reden.
FORBIDDEN_CAPABILITIES = frozenset(
    {
        "ALL",
        "SYS_ADMIN",
        "SYS_MODULE",
        "SYS_RAWIO",
        "SYS_PTRACE",
        "SYS_BOOT",
        "SYS_TIME",
        "DAC_READ_SEARCH",
        "MAC_ADMIN",
        "MAC_OVERRIDE",
        "BPF",
        "PERFMON",
        "SYSLOG",
        "LINUX_IMMUTABLE",
        "AUDIT_CONTROL",
        "SETFCAP",
    }
)

#: Apparaten die nooit handmatig aan een container gegeven mogen worden.
_FORBIDDEN_DEVICE_PATTERNS = (
    re.compile(r"^/dev/(zfs|mem|kmem|port|kmsg|console|tty|ptmx|fuse|vhost-net|vsock|autofs)$"),
    re.compile(r"^/dev/(sd[a-z]+\d*|nvme\d+(n\d+)?(p\d+)?|hd[a-z]+\d*|vd[a-z]+\d*|md\d*|dm-\d+|loop\d*|zd\d+)$"),
    re.compile(r"^/dev/(disk|mapper|zvol|cpu|mapper|bsg|sg\d*|block)(/.*)?$"),
)
#: TrueNAS staat deze niet toe als handmatig apparaat; GPU gaat via ``gpu: intel_render``.
_FORM_MANAGED_DEVICES = ("/dev/dri", "/dev/kfd", "/dev/bus/usb", "/dev/snd", "/dev/net/tun")
#: Hostapparaten die met ``privilege_reason`` mogen (allowlist; veiligste uitleg van contract §9 ``devices: []``):
#: seriële USB-adapters (Zigbee/Z-Wave/P1-meter), video-capture, DVB-tuners en de Coral-TPU. Al het andere
#: (schijven, /dev/mem, watchdog, IPMI, Intel ME, vfio, consoles, …) wordt geweigerd (ADR-0007).
_ALLOWED_HOST_DEVICES = (
    re.compile(r"^/dev/tty(USB|ACM|AMA)\d{1,3}$"),
    re.compile(r"^/dev/serial/by-(id|path)/[A-Za-z0-9][A-Za-z0-9._:+-]{0,200}$"),
    re.compile(r"^/dev/video\d{1,3}$"),
    re.compile(r"^/dev/dvb/adapter\d{1,2}/(frontend|demux|dvr|ca|net)\d{1,2}$"),
    re.compile(r"^/dev/apex_\d{1,2}$"),
)
#: Capabilities die samen met host network het netwerk van de NAS zelf kunnen wijzigen.
_HOST_NETWORK_FORBIDDEN_CAPS = frozenset({"NET_ADMIN"})
#: Hidden mappen en TrueNAS-systeemdatasets die nooit gekoppeld mogen worden (ook niet via de datasetkiezer).
_SYSTEM_DATASET_NAMES = frozenset({"ix-applications", "ix-apps", "ix-virt"})

#: Variabele-interpolatie (compose/shell): ``$VAR``, ``${VAR}``, ``$(cmd)``, ``$$``.
_INTERPOLATION = re.compile(r"\$[{(A-Za-z_$]")

_SECRET_ENV = re.compile(r"(PASS(WORD|WD)?|SECRET|TOKEN|API_?KEY|PRIVATE_?KEY|CREDENTIAL|AUTH_KEY|SALT)$", re.I)
_PLACEHOLDER = re.compile(r"<[A-Za-z0-9_ -]{1,40}>|\$\{[^}]*\}|\{\{[^}]*\}\}|%\([a-z_]+\)s|\bCHANGEME\b|\bchangeme\b")
_EXAMPLE_HOSTS = re.compile(
    r"(^|[/@.])(example\.(com|org|net)|truenas\.local|nas\.local|nas\.lan|my-?nas|your-?nas|mynas\.[a-z]+)(\b|$)",
    re.I,
)
_EXAMPLE_POOL = re.compile(r"(^|/)mnt/(tank|pool|pool1|data|nas|zpool|main|storage|POOL|<POOL>)(/|$)")
_TIMEZONE = re.compile(r"^(UTC|Etc/UTC|[A-Za-z_]+(/[A-Za-z0-9_+-]+){1,2})$")
_IPV4 = re.compile(r"(?<![0-9.])(\d{1,3}(?:\.\d{1,3}){3})(?![0-9.])")


# ---------------------------------------------------------------------------------------------- hulpfuncties
def is_own_image(repository: str) -> bool:
    return repository.startswith(OWN_IMAGE_PREFIXES)


def example_reason(value: str) -> str | None:
    """Geeft een Nederlandse reden als ``value`` een voorbeeld-/plaatshouderwaarde lijkt (D01–D18 onbekend)."""
    if _PLACEHOLDER.search(value):
        return "bevat een plaatshouder"
    if _EXAMPLE_POOL.search(value):
        return "bevat een voorbeeld-poolnaam onder /mnt (poolnaam is onbekend, D02)"
    if _EXAMPLE_HOSTS.search(value):
        return "bevat een voorbeeld-hostnaam"
    for match in _IPV4.finditer(value):
        try:
            ip = ipaddress.ip_address(match.group(1))
        except ValueError:
            continue
        if ip.is_private or ip.is_loopback or ip.is_link_local or ip in ipaddress.ip_network("100.64.0.0/10"):
            return f"bevat een vast IP-adres ({ip}); NAS- en LAN-adressen zijn onbekend (D03)"
    return None


def _has_bad_char(value: str) -> bool:
    """Stuur- en opmaaktekens (Unicode-categorie C*, o.a. NUL, tab, newline, bidi/zero-width) en backslashes."""
    return any(ch == "\\" or unicodedata.category(ch).startswith("C") for ch in value)


def normalize_abs_path(path: str) -> str | None:
    """Normaliseer een absoluut POSIX-pad, of ``None`` als het pad niet eenduidig is.

    Geweigerd (``None``): relatieve paden, ``.``/``..``-segmenten, lege segmenten (``//etc`` blijft volgens POSIX
    ``//etc`` en zou zo elke prefixvergelijking omzeilen), stuur-/opmaaktekens, backslashes en segmenten met spaties
    aan begin of eind. Eén afsluitende ``/`` is toegestaan.
    """
    if not path.startswith("/") or _has_bad_char(path):
        return None
    if path == "/":
        return "/"
    parts = path.split("/")[1:]
    if parts and parts[-1] == "":
        parts = parts[:-1]
    if not parts or any(part in ("", ".", "..") or part != part.strip() for part in parts):
        return None
    return "/" + "/".join(parts)


def mutable_tag_reason(tag: str) -> str | None:
    """Nederlandse reden als ``tag`` verschuift (``latest``, ``alpine-latest``, ``stable-alpine``, ``bookworm``)."""
    low = tag.strip().lower()
    if low in MUTABLE_TAGS or low.startswith("latest"):
        return f"Tag {tag!r} verandert in de tijd; gebruik een exacte versie."
    if _DOTTED_VERSION.search(low):
        return None  # bevat een exacte versie (x.y.z), bv. 2.479.1-lts-jdk17
    words = set(_TAG_SPLIT.split(low)) & MUTABLE_TAG_PARTS
    if words:
        return f"Tag {tag!r} bevat {sorted(words)[0]!r} en verschuift dus mee; gebruik een exacte versie."
    if not any(ch.isdigit() for ch in low):
        return f"Tag {tag!r} bevat geen versienummer en verschuift mee; gebruik een exacte versie."
    return None


def host_path_problem(path: str) -> tuple[str, str] | None:
    """Controleer een hostpad. Geeft ``(code, uitleg)`` of ``None`` als het pad acceptabel is.

    Acceptabel = een dataset onder ``/mnt/<pool>/<dataset>[/...]`` die niet in een verboden gebied ligt.
    """
    norm = normalize_abs_path(path)
    if norm is None:
        if any(marker in path.lower() for marker in _SOCKET_MARKERS):
            return (
                "docker_socket_forbidden",
                f"Hostpad {path!r} is een container-runtime-socket; dat geeft root op de NAS.",
            )
        return (
            "forbidden_host_path",
            f"Hostpad {path!r} is geen genormaliseerd absoluut pad (geen '.', '..', '//', stuurtekens of backslash).",
        )
    # hoofdletterongevoelig vergelijken: SMB-datasets kunnen hoofdletterongevoelig zijn
    lowered = norm.lower()
    if any(marker in lowered for marker in _SOCKET_MARKERS):
        return "docker_socket_forbidden", f"Hostpad {path!r} is een container-runtime-socket; dat geeft root op de NAS."
    if lowered in FORBIDDEN_EXACT_HOST_PATHS:
        return "forbidden_host_path", f"Hostpad {norm!r} is verboden (te breed: hele systeem of alle pools)."
    for forbidden in FORBIDDEN_HOST_PATHS:
        if lowered == forbidden or lowered.startswith(forbidden + "/") or forbidden.startswith(lowered + "/"):
            return "forbidden_host_path", f"Hostpad {norm!r} valt in het verboden gebied {forbidden!r}."
    parts = norm.split("/")[1:]
    if parts[0] != "mnt" or len(parts) < 3:
        return (
            "forbidden_host_path",
            f"Hostpad {norm!r} ligt niet in een dataset (verwacht /mnt/<pool>/<dataset>/…).",
        )
    for part in parts[1:]:
        if part.startswith(".") or part.lower() in _SYSTEM_DATASET_NAMES:
            return (
                "forbidden_host_path",
                f"Hostpad {norm!r} bevat {part!r}: verborgen mappen en systeemdatasets van TrueNAS "
                "(.ix-apps, .zfs, .system, ix-applications) zijn verboden.",
            )
    return None


def mount_path_problem(path: str) -> str | None:
    norm = normalize_abs_path(path)
    if norm is None:
        return f"Koppelpad {path!r} is geen genormaliseerd absoluut pad."
    if any(marker in norm.lower() for marker in _SOCKET_MARKERS):
        return f"Koppelpad {path!r} is een container-runtime-socket."
    if norm in FORBIDDEN_MOUNT_PATHS or norm.startswith(("/proc/", "/sys/", "/dev/")):
        return f"Koppelpad {norm!r} overschrijft een systeemmap in de container."
    return None


def device_problem(device: str, *, host: bool = True) -> str | None:
    """Controleer een apparaatpad. ``host=True``: het apparaat op de NAS (allowlist); ``False``: het pad in de container."""
    norm = normalize_abs_path(device)
    if norm is None or not norm.startswith("/dev/"):
        return f"Apparaat {device!r} is geen genormaliseerd pad onder /dev (geen '.', '..', '//' of stuurtekens)."
    for managed in _FORM_MANAGED_DEVICES:
        if norm == managed or norm.startswith(managed + "/"):
            return (
                f"Apparaat {norm!r} mag niet handmatig worden toegevoegd; TrueNAS staat dat in het formulier niet toe "
                "(GPU via 'gpu: intel_render')."
            )
    for pattern in _FORBIDDEN_DEVICE_PATTERNS:
        if pattern.match(norm):
            return f"Apparaat {norm!r} geeft directe toegang tot opslag, geheugen of de kernel."
    if host and not any(pattern.match(norm) for pattern in _ALLOWED_HOST_DEVICES):
        return (
            f"Apparaat {norm!r} staat niet op de lijst van toegestane apparaten (seriële USB-adapters, video-capture, "
            "DVB-tuners, Coral-TPU); andere apparaten vereisen eerst een wijziging van het platformcontract."
        )
    return None


def _nk(key: object) -> str:
    """Sleutel normaliseren voor vergelijking: kleine letters, alleen letters/cijfers (``Host-Network`` → ``hostnetwork``)."""
    return re.sub(r"[^a-z0-9]", "", str(key).lower())


#: Docker-/compose-velden die in catalogus-waarden nooit (niet-leeg) mogen voorkomen: namespaces van de host delen
#: (pid/ipc/uts/userns/netwerk), beveiligingsprofielen uitzetten, extra capabilities/sysctls, eigen opdrachten.
#: Genormaliseerd met :func:`_nk`.
FORBIDDEN_VALUE_KEYS = frozenset(
    {
        "pid",
        "pidmode",
        "hostpid",
        "ipc",
        "ipcmode",
        "hostipc",
        "uts",
        "utsmode",
        "userns",
        "usernsmode",
        "networkmode",
        "net",
        "cgroup",
        "cgroupns",
        "cgroupnsmode",
        "cgroupparent",
        "securityopt",
        "securityopts",
        "securityoptions",
        "securitycontext",
        "apparmor",
        "apparmorprofile",
        "seccomp",
        "seccompprofile",
        "capadd",
        "capabilities",
        "sysctls",
        "devicecgrouprules",
        "volumesfrom",
        "runtime",
        "command",
        "entrypoint",
        "allowprivilegeescalation",
    }
)
_PATH_VALUE_KEYS = frozenset({"path", "hostpath"})
_DEVICE_VALUE_KEYS = {"hostdevice": True, "containerdevice": False}


def _has_content(value: Any) -> bool:
    if isinstance(value, dict):
        return any(_has_content(v) for v in value.values())
    if isinstance(value, list):
        return len(value) > 0
    return value is not None and value is not False and value != ""


def catalog_value_findings(values: Any, *, host_network_reason: str | None, resolved: bool) -> list[Finding]:
    """Controleer vrije catalogus-``values`` (voor én na het invullen van parameters).

    ``resolved=False`` (validator): letterlijke hostpaden, voorbeeldwaarden en geheimen zijn fout; ``{"param": …}``
    wordt overgeslagen (dat controleert de validator apart). ``resolved=True`` (vertaling): de ingevulde waarden
    komen uit wizard/datasetkiezer en worden opnieuw op verboden gebieden en velden gecontroleerd (verdediging in
    de diepte; een parameter mag nooit iets verbodens binnensmokkelen).
    """
    out: list[Finding] = []
    reason = (host_network_reason or "").strip()

    def leaf(value: str, path: str, key: str, raw_key: str) -> None:
        low = value.lower()
        if any(marker in low for marker in _SOCKET_MARKERS):
            out.append(error("docker_socket_forbidden", path, "Verwijzing naar een container-runtime-socket."))
            return
        if "unconfined" in low:
            out.append(
                error("catalog_value_forbidden", path, "Een beveiligingsprofiel uitzetten ('unconfined') is verboden.")
            )
        if _INTERPOLATION.search(value):
            out.append(
                error(
                    "interpolation_forbidden",
                    path,
                    "Variabele-interpolatie ($VAR, ${…}, $(…), $$) is niet toegestaan; gebruik een getypte parameter.",
                )
            )
        if key in _PATH_VALUE_KEYS and value.startswith("/"):
            problem = host_path_problem(value)
            if problem:
                out.append(error(problem[0], path, problem[1]))
            elif not resolved:
                out.append(
                    error(
                        "literal_host_path",
                        path,
                        f"Hostpad {value!r} staat letterlijk in de waarden; gebruik een parameter.",
                    )
                )
        elif key == "mountpath":
            if problem_nl := mount_path_problem(value):
                out.append(error("forbidden_mount_path", path, problem_nl))
        elif key in _DEVICE_VALUE_KEYS or (key == "devices" and value.startswith("/")):
            device = value.split(":", 1)[0] if key == "devices" else value
            if problem_nl := device_problem(device, host=_DEVICE_VALUE_KEYS.get(key, True)):
                out.append(error("device_forbidden", path, problem_nl))
        elif key == "tag":
            if tag_reason := mutable_tag_reason(value):
                out.append(error("mutable_tag_forbidden", path, tag_reason))
        elif not resolved and (example := example_reason(value)):
            out.append(error("example_value_as_real", path, f"Waarde {example}."))
        if not resolved and _SECRET_ENV.search(raw_key.lower()) and value.strip():
            out.append(
                error("secret_literal_forbidden", path, "Geheim als letterlijke waarde in de catalogus-waarden.")
            )

    def visit(value: Any, path: str, key: str, raw_key: str) -> None:
        if isinstance(value, dict):
            if not resolved and set(value) == {"param"} and isinstance(value["param"], str):
                return
            for k, sub in value.items():
                sub_path = f"{path}.{k}"
                nk = _nk(k)
                if "compose" in nk or nk == "services":
                    out.append(
                        error("compose_forbidden", sub_path, "Compose-configuratie in catalogus-waarden is verboden.")
                    )
                elif nk == "privileged":
                    if sub is not None and sub is not False:
                        out.append(error("privileged_forbidden", sub_path, "Privileged-modus is verboden."))
                elif nk == "hostnetwork":
                    if sub is not None and sub is not False and not reason:
                        out.append(
                            error(
                                "host_network_without_reason",
                                sub_path,
                                "Host network in catalogus-waarden zonder host_network_reason.",
                            )
                        )
                elif nk in FORBIDDEN_VALUE_KEYS:
                    if _has_content(sub):
                        out.append(
                            error(
                                "catalog_value_forbidden",
                                sub_path,
                                f"Het veld {k!r} (host-namespaces, beveiligingsprofielen, capabilities, sysctls of eigen "
                                "opdrachten) is in catalogus-waarden verboden.",
                            )
                        )
                else:
                    visit(sub, sub_path, nk, str(k))
        elif isinstance(value, list):
            for idx, sub in enumerate(value):
                visit(sub, f"{path}[{idx}]", key, raw_key)
        elif isinstance(value, str):
            leaf(value, path, key, raw_key)

    visit(values, "values", "", "")
    return out


def _param_refs(value: Any, path: str, key: str = "") -> Iterator[tuple[str, str, str]]:
    """Alle ``{"param": "<naam>"}``-verwijzingen in vrije data (catalogus-``values``), met de genormaliseerde sleutel."""
    if isinstance(value, dict):
        if set(value) == {"param"} and isinstance(value["param"], str):
            yield path, value["param"], key
            return
        for sub_key, sub in value.items():
            yield from _param_refs(sub, f"{path}.{sub_key}", _nk(sub_key))
    elif isinstance(value, list):
        for idx, sub in enumerate(value):
            yield from _param_refs(sub, f"{path}[{idx}]", key)


# ---------------------------------------------------------------------------------------------- regels
#: contract 1.3 §9: restrisico dat een upstream-image als Custom App in ``privilege_reason`` noemt
NNP_RISK_NL = "zonder no-new-privileges kan een setuid-programma binnen de container root worden"
_HUB_RESERVED = re.compile(r"^hub_", re.I)


class _Rules:
    def __init__(self, m: Manifest) -> None:
        self.m = m
        self.out: list[Finding] = []
        self.params: dict[str, Parameter] = {p.name: p for p in m.parameters}
        self.used_params: set[str] = set()
        #: indexen in ``out`` van waarschuwingen die ``accepted_findings`` mag accepteren (contract 1.3 §9)
        self.acceptable: set[int] = set()

    # -- registratie
    def err(self, code: str, path: str, msg: str) -> None:
        self.out.append(error(code, path, msg))

    def warn(self, code: str, path: str, msg: str, *, acceptable: bool = False) -> None:
        if acceptable:
            self.acceptable.add(len(self.out))
        self.out.append(warning(code, path, msg))

    def ref(self, ref: ParamRef, path: str, allowed_types: Iterable[str], what: str) -> Parameter | None:
        param = self.params.get(ref.param)
        self.used_params.add(ref.param)
        if param is None:
            self.err("unknown_parameter", path, f"{what} verwijst naar onbekende parameter {ref.param!r}.")
            return None
        allowed = tuple(allowed_types)
        if param.type not in allowed:
            self.err(
                "parameter_type_mismatch",
                path,
                f"{what} verwacht een parameter van type {' of '.join(allowed)}; {ref.param!r} is {param.type!r}.",
            )
        return param

    # -- hoofdroute
    def run(self) -> list[Finding]:
        m = self.m
        self.parameters()
        self.duplicates()
        if m.install.method == "custom_app":
            self.custom_app()
        else:
            self.catalog_app()
        self.network()
        self.privileges()
        self.storage_decls()
        self.backup()
        self.healthcheck()
        self.dependencies()
        self.risk_and_upgrade()
        self.meta()
        self.contract_1_3()
        for name, param in self.params.items():
            if name not in self.used_params and param.decision is None:
                self.warn("parameter_unused", f"parameters[{name}]", f"Parameter {name!r} wordt nergens gebruikt.")
        self.accept()
        return self.out

    # -- contract 1.3 §9
    def contract_1_3(self) -> None:
        m = self.m
        if m.install.method == "catalog_app" and not (m.install.reason or "").strip():
            self.warn(
                "install_reason_ontbreekt",
                "install.reason",
                "Een catalogusapp vraagt install.reason: 'meerdere_containers' of een reden waarom een catalogusapp "
                "nodig is bij één container (standaard is een Custom App).",
            )
        upstream = m.install.method == "custom_app" and m.image is not None and not is_own_image(m.image.repository)
        if upstream and "no-new-privileges" not in (m.privilege_reason or "").lower():
            self.warn(
                "restrisico_nnp_ontbreekt",
                "privilege_reason",
                "Een upstream-image als Custom App draait zonder no-new-privileges (het formulier zet die niet). "
                f"Noem in privilege_reason het restrisico: '{NNP_RISK_NL}'.",
            )
        # gereserveerde namen (§3.2): HUB_* is het koppelen van de Hub, nooit een parameter of env
        for idx, p in enumerate(m.parameters):
            if _HUB_RESERVED.match(p.name):
                self.err("reserved_env", f"parameters[{idx}].name", f"{p.name!r}: HUB_* is gereserveerd (§3.2).")
            if p.secret and p.source not in ("generated", "secret"):
                self.err(
                    "secret_param_invalid",
                    f"parameters[{idx}].secret",
                    f"Parameter {p.name!r} is geheim maar heeft source {p.source!r}; gebruik 'generated' (de Hub maakt "
                    "hem in zijn vault) of 'secret'.",
                )
            if p.secret and p.example is not None:
                self.err(
                    "secret_literal_forbidden",
                    f"parameters[{idx}].example",
                    f"Geheime parameter {p.name!r} heeft een voorbeeldwaarde; de waarde staat nooit in het manifest.",
                )
        if m.custom_app is not None:
            for idx, env in enumerate(m.custom_app.env):
                if _HUB_RESERVED.match(env.name):
                    self.err(
                        "reserved_env",
                        f"custom_app.env[{idx}].name",
                        f"{env.name} is gereserveerd: HUB_URL, HUB_ENROLLMENT_TOKEN en HUB_KEY_FINGERPRINT zet de "
                        "vertaling zelf als de Hub koppelt (§3.2).",
                    )
        if m.values is not None:
            for path in _hub_names_in_values(m.values, "values"):
                self.err(
                    "reserved_env", path, "HUB_* is gereserveerd en hoort niet in de waarden van een catalogusapp."
                )
        for idx, port in enumerate(m.network.ports):
            if port.container_port == "same_as_host" and m.install.method != "catalog_app":
                self.err(
                    "same_as_host_not_allowed",
                    f"network.ports[{idx}].container_port",
                    "container_port 'same_as_host' mag alleen bij een catalogusapp waarvan de container op de gekozen "
                    "hostpoort luistert; geef bij een Custom App de echte containerpoort.",
                )
        seen: set[str] = set()
        for idx, check in enumerate(m.post_install_checks):
            if check.id in seen:
                self.err("duplicate_name", f"post_install_checks[{idx}].id", f"Controle {check.id!r} komt dubbel voor.")
            seen.add(check.id)
        if m.healthcheck.monitor is None:
            self.warn(
                "healthcheck_monitor_missing",
                "healthcheck.monitor",
                "Geen monitorplan: geef healthcheck.monitor {pad, verwachte_status, timeout_s, interval_s}; de Hub maakt "
                "er een monitorplan van dat de beheerder in Uptime Kuma overneemt.",
            )

    def accept(self) -> None:
        """``accepted_findings`` (contract 1.3 §9): alleen waarschuwingen uit de vaste lijst, alleen waar de voorwaarde
        geldt (``secret_in_form`` alleen bij een parameter met ``source: generated`` en ``secret: true``). Een
        geaccepteerde code die niet (meer) optreedt geeft ``accepted_finding_unused`` (faalt in --strict). Fouten zijn
        nooit te accepteren."""
        accepted: dict[str, str] = {}
        for idx, a in enumerate(self.m.accepted_findings):
            if a.code in accepted:
                self.err("duplicate_name", f"accepted_findings[{idx}].code", f"Code {a.code!r} staat er dubbel in.")
            accepted.setdefault(a.code, a.reden_nl)
        used: set[str] = set()
        for idx, f in enumerate(self.out):
            if f.severity is Severity.WARNING and f.code in accepted and idx in self.acceptable:
                self.out[idx] = f.model_copy(update={"accepted": True, "accepted_reden_nl": accepted[f.code]})
                used.add(f.code)
        for idx, a in enumerate(self.m.accepted_findings):
            if a.code not in used:
                self.warn(
                    "accepted_finding_unused",
                    f"accepted_findings[{idx}]",
                    f"{a.code!r} wordt geaccepteerd maar treedt niet (meer) op"
                    + (
                        " bij een geheim dat de Hub maakt (source generated + secret true)"
                        if a.code == "secret_in_form"
                        else ""
                    )
                    + "; haal de regel weg.",
                )

    # -- parameters
    def parameters(self) -> None:
        for idx, p in enumerate(self.m.parameters):
            path = f"parameters[{idx}]"
            if p.source == "default":
                if p.default is None:
                    self.err(
                        "parameter_default_missing",
                        path,
                        f"Parameter {p.name!r} heeft source 'default' maar geen default.",
                    )
            elif p.default is not None:
                self.err(
                    "parameter_default_not_allowed",
                    f"{path}.default",
                    f"Parameter {p.name!r} (source {p.source!r}) mag geen default hebben: die waarde komt uit "
                    f"{'de wizard' if p.source == 'wizard' else 'de datasetkiezer' if p.source == 'dataset_picker' else 'de app zelf'}"
                    ", nooit uit het manifest.",
                )
            if isinstance(p.default, str) and (reason := example_reason(p.default)):
                self.err("example_value_as_real", f"{path}.default", f"Default van {p.name!r} {reason}.")
            if p.type in ("path", "dataset") and p.source not in ("dataset_picker", "wizard"):
                self.err(
                    "host_path_param_wrong_source",
                    f"{path}.source",
                    f"Pad-/datasetparameter {p.name!r} moet uit de datasetkiezer of wizard komen, niet uit {p.source!r}.",
                )
            if p.type == "gpu" and (p.source != "wizard" or p.decision != "D08" or p.required):
                self.err(
                    "gpu_param_invalid",
                    path,
                    f"GPU-parameter {p.name!r} moet source 'wizard', decision 'D08' en required false hebben: de wizard "
                    "biedt intel_render alleen aan als D08 een render-node heeft gemeten (contract 1.2 §9).",
                )
            if p.type == "secret_ref" and p.source not in ("secret", "generated"):
                self.err(
                    "secret_literal_forbidden",
                    f"{path}.source",
                    f"Geheim {p.name!r} moet source 'secret' of 'generated' hebben.",
                )
            if p.decision and p.decision not in self.m.decisions_required:
                self.err(
                    "decision_not_listed",
                    f"{path}.decision",
                    f"Parameter {p.name!r} hangt af van {p.decision}, maar dat besluit staat niet in decisions_required.",
                )

    def duplicates(self) -> None:
        def dupes(names: list[str], path: str, what: str) -> None:
            seen: set[str] = set()
            for n in names:
                if n in seen:
                    self.err("duplicate_name", path, f"{what} {n!r} komt dubbel voor.")
                seen.add(n)

        dupes([p.name for p in self.m.parameters], "parameters", "Parameter")
        dupes([s.name for s in self.m.storage], "storage", "Opslag")
        dupes([p.name for p in self.m.network.ports], "network.ports", "Poort")
        dupes([d.app_id for d in self.m.dependencies], "dependencies", "Afhankelijkheid")
        if self.m.custom_app:
            dupes([s.name for s in self.m.custom_app.storage], "custom_app.storage", "Opslagregel")
            dupes([e.name for e in self.m.custom_app.env], "custom_app.env", "Omgevingsvariabele")

    # -- custom app (het standaardformulier)
    def custom_app(self) -> None:
        m = self.m
        assert m.image is not None and m.custom_app is not None
        img, ca = m.image, m.custom_app
        if not img.tag.strip():
            self.err("tag_missing", "image.tag", "Image-tag ontbreekt; gebruik een exacte versie.")
        elif tag_reason := mutable_tag_reason(img.tag):
            self.err("mutable_tag_forbidden", "image.tag", tag_reason)
        elif _IMPRECISE_TAG.match(img.tag) and not _EXACT_VERSION.match(img.tag):
            self.warn(
                "imprecise_version_tag",
                "image.tag",
                f"Tag {img.tag!r} is geen exacte versie (major/minor schuift mee).",
            )
        if "@" in img.repository or re.search(r":[^/]*$", img.repository.split("/", 1)[-1]):
            self.err(
                "tag_in_repository",
                "image.repository",
                "Repository bevat een tag of digest; zet die in image.tag/image.digest.",
            )
        if img.digest is None:
            if is_own_image(img.repository):
                self.warn(
                    "digest_pending_release",
                    "image.digest",
                    "Eigen image zonder digest: de release-CI vult die in; installeren vereist de digest.",
                    acceptable=True,
                )
            else:
                self.err(
                    "digest_missing", "image.digest", f"Upstream-image {img.repository!r} vereist een sha256-digest."
                )
        # poorten: alleen network.ports (contract 1.2 §9); custom_app.ports is vervallen (overgang)
        if ca.ports is not None:
            if _ports_key(ca.ports) != _ports_key(m.network.ports):
                self.err(
                    "ports_inconsistent",
                    "custom_app.ports",
                    "custom_app.ports is vervallen (contract 1.2 §9) en wijkt bovendien af van network.ports; "
                    "verwijder custom_app.ports, de formulierpoorten komen uit network.ports.",
                )
            else:
                self.warn(
                    "deprecated_field",
                    "custom_app.ports",
                    "custom_app.ports is vervallen (contract 1.2 §9): de formulierpoorten komen uit network.ports. "
                    "Verwijder dit veld (in --strict is dit een fout).",
                )
        # opslag
        decls = {s.name: s for s in m.storage}
        bound: set[str] = set()
        for idx, sb in enumerate(ca.storage):
            path = f"custom_app.storage[{idx}]"
            decl = decls.get(sb.name)
            bound.add(sb.name)
            if decl is None:
                self.err(
                    "storage_unknown_reference", path, f"Opslagregel {sb.name!r} heeft geen declaratie in storage[]."
                )
            if sb.type == "host_path":
                if isinstance(sb.host_path, ParamRef):
                    param = self.ref(sb.host_path, f"{path}.host_path", ("path",), "Hostpad")
                    if param is not None:
                        if param.source != "dataset_picker":
                            self.err(
                                "host_path_param_wrong_source",
                                f"{path}.host_path",
                                f"Hostpad-parameter {param.name!r} moet source 'dataset_picker' hebben.",
                            )
                        role = decl.required_dataset_role if decl else None
                        if decl is not None and role is None:
                            self.err(
                                "dataset_role_missing",
                                f"storage[{sb.name}].required_dataset_role",
                                f"Hostpad-opslag {sb.name!r} heeft geen required_dataset_role.",
                            )
                        if role is not None and param.dataset_role != role:
                            self.err(
                                "dataset_role_inconsistent",
                                f"{path}.host_path",
                                f"Datasetrol van parameter {param.name!r} ({param.dataset_role!r}) verschilt van "
                                f"storage {sb.name!r} ({role!r}).",
                            )
                elif isinstance(sb.host_path, str):
                    problem = host_path_problem(sb.host_path)
                    if problem:
                        self.err(problem[0], f"{path}.host_path", problem[1])
                    else:
                        self.err(
                            "literal_host_path",
                            f"{path}.host_path",
                            f"Hostpad {sb.host_path!r} staat letterlijk in het manifest; poolnaam en datasets zijn "
                            "onbekend (D02). Gebruik een parameter met source 'dataset_picker'.",
                        )
            elif sb.type == "tmpfs" and decl is not None and decl.kind != "scratch":
                self.err(
                    "invalid_value",
                    f"{path}.type",
                    f"tmpfs is alleen toegestaan voor opslag van soort 'scratch' ({sb.name!r}).",
                )
            if sb.type == "ix_volume" and decl is not None and decl.kind == "media":
                self.warn(
                    "invalid_value",
                    f"{path}.type",
                    f"Media-opslag {sb.name!r} op een ixVolume valt buiten de mediadatasets.",
                )
        for name, decl in decls.items():
            if name not in bound:
                self.err(
                    "storage_not_mapped", f"storage[{name}]", f"Opslag {name!r} heeft geen regel in custom_app.storage."
                )
            _ = decl
        # omgevingsvariabelen
        for idx, env in enumerate(ca.env):
            path = f"custom_app.env[{idx}]"
            if env.value is not None:
                low = env.value.lower()
                if env.name.upper() == "DOCKER_HOST" or any(marker in low for marker in _SOCKET_MARKERS):
                    self.err("docker_socket_forbidden", path, f"{env.name} verwijst naar een container-runtime-socket.")
                if _SECRET_ENV.search(env.name) and env.value.strip():
                    self.err(
                        "secret_literal_forbidden",
                        path,
                        f"{env.name} lijkt een geheim; geheimen horen nooit in het manifest of het Custom App-formulier. "
                        "De app maakt ze zelf aan in /appdata/secrets.",
                    )
                elif reason := example_reason(env.value):
                    self.err("example_value_as_real", f"{path}.value", f"Waarde van {env.name} {reason}.")
                if _INTERPOLATION.search(env.value):
                    self.err(
                        "interpolation_forbidden",
                        f"{path}.value",
                        f"Waarde van {env.name} bevat variabele-interpolatie ($VAR, ${{…}}, $(…), $$); TrueNAS zet "
                        "omgevingswaarden in een compose-bestand, dus dat is verboden. Gebruik een getypte parameter.",
                    )
            if env.value_from is not None:
                param = self.ref(
                    env.value_from,
                    f"{path}.value_from",
                    ("string", "integer", "boolean", "port", "url", "timezone", "duration", "secret_ref", "dataset"),
                    f"Omgevingsvariabele {env.name}",
                )
                if param is not None and param.hub_generated_secret:
                    # contract 1.3 §3.1 a: de Hub maakt het geheim (vault) en geeft het alleen in de values mee
                    self.warn(
                        "secret_in_form",
                        path,
                        f"{env.name} krijgt een geheim dat de Hub maakt ({param.name!r}, source generated); het staat "
                        "nooit in het manifest, een log of evidence.",
                        acceptable=True,
                    )
                elif param is not None and (
                    param.type == "secret_ref" or param.source in ("secret", "generated") or param.secret
                ):
                    self.err(
                        "secret_in_form",
                        path,
                        f"{env.name} zou een geheim ({param.name!r}) in het Custom App-formulier zetten; dat is verboden.",
                    )
        # gpu (contract 1.2 §9: privileges.gpu is het maximum), run_as, tijdzone, portal
        if isinstance(ca.gpu, ParamRef):
            self.ref(ca.gpu, "custom_app.gpu", ("gpu",), "GPU")
            wants_gpu = True  # de wizard kan intel_render kiezen
        else:
            wants_gpu = ca.gpu != "none"
        if wants_gpu and m.privileges.gpu != "intel_render":
            self.err(
                "gpu_exceeds_privileges",
                "custom_app.gpu",
                "custom_app.gpu vraagt (mogelijk) een GPU, maar privileges.gpu (het maximum) is 'none'.",
            )
        if ca.run_as.mode == "image_default" and not is_own_image(img.repository):
            self.warn(
                "run_as_image_default_upstream",
                "custom_app.run_as",
                "Upstream-image draait als de gebruiker uit de image (mogelijk root); overweeg run_as custom 568:568.",
            )
        if ca.timezone is None:
            self.warn("timezone_missing", "custom_app.timezone", "Geen tijdzone: het formulier krijgt 'Etc/UTC'.")
        elif isinstance(ca.timezone, ParamRef):
            self.ref(ca.timezone, "custom_app.timezone", ("timezone",), "Tijdzone")
        elif not _TIMEZONE.match(ca.timezone):
            self.err("invalid_value", "custom_app.timezone", f"Tijdzone {ca.timezone!r} is geen IANA-tijdzone.")
        elif ca.timezone != "Etc/UTC":
            self.warn(
                "example_value_as_real",
                "custom_app.timezone",
                f"Vaste tijdzone {ca.timezone!r}; laat de wizard die kiezen (parameter met type 'timezone').",
            )
        if ca.portal is not None and ca.portal.port not in {p.name for p in m.network.ports}:
            self.err(
                "portal_unknown_port",
                "custom_app.portal.port",
                f"Portal verwijst naar onbekende poort {ca.portal.port!r}.",
            )

    def port(self, port: PortBinding, path: str) -> None:
        if isinstance(port.host_port, ParamRef):
            self.ref(port.host_port, f"{path}.host_port", ("port",), "Hostpoort")
        elif port.host_port < 1024:
            self.warn("port_privileged_range", f"{path}.host_port", f"Hostpoort {port.host_port} ligt onder 1024.")

    # -- catalogusapp
    def catalog_app(self) -> None:
        m = self.m
        assert m.catalog is not None and m.values is not None
        if m.catalog.app == "ix-app":
            self.err(
                "install_method_forbidden",
                "catalog.app",
                "De catalogusapp 'ix-app' is het standaard Custom App-formulier; gebruik install.method 'custom_app' "
                "(getypt formulier). Ruwe ix-app-waarden (privileged, command, devices, …) zijn verboden.",
            )
        version = m.catalog.version.lower()
        if version in MUTABLE_TAGS or not _EXACT_VERSION.match(m.catalog.version):
            self.err(
                "mutable_version_forbidden",
                "catalog.version",
                f"Catalogusversie {m.catalog.version!r} moet een exacte versie zijn (bv. 1.2.3).",
            )
        self.out.extend(
            catalog_value_findings(m.values, host_network_reason=m.network.host_network_reason, resolved=False)
        )
        for path, name, key in _param_refs(m.values, "values"):
            param = self.params.get(name)
            self.used_params.add(name)
            if param is None:
                self.err("unknown_parameter", path, f"Waarde verwijst naar onbekende parameter {name!r}.")
                continue
            if key in _PATH_VALUE_KEYS:
                if param.type != "path":
                    self.err(
                        "parameter_type_mismatch",
                        path,
                        f"Hostpad verwacht een parameter van type 'path'; {name!r} is {param.type!r}.",
                    )
                elif param.source != "dataset_picker":
                    self.err(
                        "host_path_param_wrong_source",
                        path,
                        f"Hostpad-parameter {name!r} moet source 'dataset_picker' hebben.",
                    )
            elif key in _DEVICE_VALUE_KEYS or key in ("devices", "mountpath"):
                self.err(
                    "catalog_value_forbidden",
                    path,
                    "Apparaten en koppelpaden moeten letterlijk (en dus beoordeelbaar) in het manifest staan.",
                )
            if param.source in ("secret", "generated") or param.type == "secret_ref" or param.secret:
                self.warn(
                    "secret_in_form",
                    path,
                    f"Geheim {name!r} gaat als (door TrueNAS als privé gemarkeerde) catalogus-waarde mee; "
                    "controleer dat het niet in logs of evidence verschijnt."
                    + (" De Hub maakt het (vault, contract 1.3 §3.1 a)." if param.hub_generated_secret else ""),
                    acceptable=param.hub_generated_secret,
                )

    # -- netwerk
    def network(self) -> None:
        net = self.m.network
        if net.host_network:
            if not (net.host_network_reason or "").strip():
                self.err(
                    "host_network_without_reason", "network.host_network", "Host network zonder host_network_reason."
                )
            else:
                self.warn(
                    "host_network_requested",
                    "network.host_network",
                    "Host network gevraagd; vereist expliciete goedkeuring.",
                )
        if net.joins_platform_network:
            self.err(
                "platform_network_not_supported",
                "network.joins_platform_network",
                "Apps zitten niet in een gedeeld Docker-netwerk (contract §2); ze bereiken elkaar via NAS-adres + hostpoort.",
            )
        elif net.joins_platform_network is False:
            self.warn(
                "deprecated_field",
                "network.joins_platform_network",
                "network.joins_platform_network is vervallen (contract 1.2 §9): apps delen nooit een Docker-netwerk. "
                "Verwijder dit veld (in --strict is dit een fout).",
            )
        seen: set[tuple[int, str]] = set()
        for idx, port in enumerate(net.ports):
            if isinstance(port.host_port, int):
                key = (port.host_port, port.protocol)
                if key in seen:
                    self.err(
                        "duplicate_port",
                        f"network.ports[{idx}]",
                        f"Hostpoort {port.host_port}/{port.protocol} komt dubbel voor.",
                    )
                seen.add(key)
            self.port(port, f"network.ports[{idx}]")

    # -- privileges
    def privileges(self) -> None:
        pr = self.m.privileges
        reason = (self.m.privilege_reason or "").strip()
        if pr.privileged:
            self.err("privileged_forbidden", "privileges.privileged", "Privileged-modus is verboden (ook met reden).")
        for idx, cap in enumerate(pr.cap_add):
            path = f"privileges.cap_add[{idx}]"
            name = cap.upper().removeprefix("CAP_")
            if name in FORBIDDEN_CAPABILITIES:
                self.err(
                    "capability_forbidden", path, f"Capability {name} geeft (bijna) root op de NAS en is verboden."
                )
            elif name not in KNOWN_CAPABILITIES:
                self.err("capability_unknown", path, f"Onbekende capability {cap!r}.")
            elif name in _HOST_NETWORK_FORBIDDEN_CAPS and self.m.network.host_network:
                self.err(
                    "capability_forbidden",
                    path,
                    f"Capability {name} samen met host network kan het netwerk van de NAS zelf wijzigen; dat is verboden.",
                )
            elif not reason:
                self.err("capability_without_reason", path, f"Extra capability {name} zonder privilege_reason.")
        for idx, dev in enumerate(pr.devices):
            path = f"privileges.devices[{idx}]"
            problem = device_problem(dev.host_device) or device_problem(dev.container_device, host=False)
            if problem:
                self.err("device_forbidden", path, problem)
            elif not reason:
                self.err("device_without_reason", path, f"Apparaat {dev.host_device} zonder privilege_reason.")
        if pr.gpu != "none":
            if not reason:
                self.err("gpu_without_reason", "privileges.gpu", "GPU-toegang (/dev/dri) zonder privilege_reason.")
            if "D08" not in self.m.decisions_required:
                self.err(
                    "gpu_decision_missing",
                    "decisions_required",
                    "privileges.gpu 'intel_render' vereist D08 (Intel-render-node, via de TrueNAS-API gemeten) in "
                    "decisions_required (contract 1.2 §9).",
                )

    # -- opslagdeclaraties
    def storage_decls(self) -> None:
        roles_from_params = {p.dataset_role for p in self.m.parameters if p.dataset_role}
        for idx, decl in enumerate(self.m.storage):
            path = f"storage[{idx}]"
            if problem := mount_path_problem(decl.mount):
                self.err("forbidden_mount_path", f"{path}.mount", problem)
            if (
                self.m.install.method == "catalog_app"
                and decl.kind in ("data", "media")
                and decl.required_dataset_role is None
            ):
                self.err(
                    "dataset_role_missing",
                    f"{path}.required_dataset_role",
                    f"Opslag {decl.name!r} ({decl.kind}) zonder datasetrol.",
                )
            if (
                self.m.install.method == "catalog_app"
                and decl.required_dataset_role
                and decl.required_dataset_role not in roles_from_params
            ):
                self.err(
                    "dataset_role_inconsistent",
                    f"{path}.required_dataset_role",
                    f"Geen parameter met dataset_role {decl.required_dataset_role!r} voor opslag {decl.name!r}.",
                )
        mounts: set[str] = set()
        for decl in self.m.storage:
            norm = normalize_abs_path(decl.mount) or decl.mount
            if norm in mounts:
                self.err("duplicate_name", "storage", f"Koppelpad {norm!r} komt dubbel voor.")
            mounts.add(norm)

    # -- back-up
    def backup(self) -> None:
        b = self.m.backup
        names = {s.name for s in self.m.storage}
        for idx, ds in enumerate(b.snapshot_datasets):
            if ds not in names:
                self.err("backup_unknown_dataset", f"backup.snapshot_datasets[{idx}]", f"Onbekende opslag {ds!r}.")
        for decl in self.m.storage:
            if decl.kind in ("config", "data") and decl.name not in b.snapshot_datasets:
                tmpfs = self.m.custom_app and any(
                    sb.name == decl.name and sb.type == "tmpfs" for sb in self.m.custom_app.storage
                )
                if not tmpfs:
                    self.warn(
                        "backup_dataset_not_covered",
                        "backup.snapshot_datasets",
                        f"Opslag {decl.name!r} ({decl.kind}) zit niet in de snapshotlijst.",
                    )
        if b.db_export.method == "none" and self.m.upgrade.migrations:
            self.warn(
                "backup_db_export_none",
                "backup.db_export",
                "Databasemigraties zonder consistente database-export; rollback vereist dan een snapshot-restore. "
                "Kan de app niet exporteren, gebruik dan stopped_snapshot (app stoppen + ZFS-snapshot).",
                acceptable=True,
            )
        if b.db_export.method == "stopped_snapshot" and not b.snapshot_datasets:
            self.err(
                "stopped_snapshot_without_dataset",
                "backup.snapshot_datasets",
                "stopped_snapshot (app stoppen + ZFS-snapshot) vraagt minstens één dataset in snapshot_datasets; de "
                "stilstand telt mee in rto.",
            )
        for field in ("rpo", "rto"):
            value = getattr(b, field)
            if isinstance(value, ParamRef):
                self.ref(value, f"backup.{field}", ("duration",), field.upper())

    def healthcheck(self) -> None:
        hc = self.m.healthcheck
        if hc.port is not None and hc.port not in {p.name for p in self.m.network.ports}:
            self.err(
                "healthcheck_unknown_port",
                "healthcheck.port",
                f"Healthcheck verwijst naar onbekende poort {hc.port!r}.",
            )

    def dependencies(self) -> None:
        for idx, dep in enumerate(self.m.dependencies):
            if dep.app_id == self.m.app_id:
                self.err("dependency_self", f"dependencies[{idx}]", "Een app kan niet van zichzelf afhangen.")

    def risk_and_upgrade(self) -> None:
        for action, minimum in RISK_MINIMUM.items():
            value = getattr(self.m.risk, action)
            if _RISK_RANK[value] < _RISK_RANK[minimum]:
                self.err(
                    "risk_below_policy_minimum",
                    f"risk.{action}",
                    f"risk.{action} is {value}; het platform vereist minimaal {minimum} (contract §8).",
                )
        if self.m.upgrade.migrations and self.m.upgrade.tag_only_rollback_safe:
            self.err(
                "tag_only_rollback_with_migrations",
                "upgrade.tag_only_rollback_safe",
                "Met databasemigraties is terugzetten van alleen de tag onveilig (EX15); zet tag_only_rollback_safe op false.",
            )

    def meta(self) -> None:
        if self.m.compatibility is None:
            self.warn(
                "compatibility_missing",
                "compatibility",
                "Geen TrueNAS-compatibiliteit opgegeven; compatibiliteit wordt 'onbekend'.",
            )
        if self.m.validation_status != "unvalidated":
            self.warn(
                "validation_status_claim_ignored",
                "validation_status",
                f"Het manifest claimt {self.m.validation_status!r}; de catalogus bepaalt de status zelf.",
            )


def _ports_key(ports: list[PortBinding]) -> list[tuple[str, str, str, str]]:
    return sorted(
        (
            p.name,
            str(p.container_port),
            p.protocol,
            p.host_port.param if isinstance(p.host_port, ParamRef) else str(p.host_port),
        )
        for p in ports
    )


def _hub_names_in_values(value: Any, path: str) -> Iterator[str]:
    """Paden in catalogus-``values`` met een gereserveerde naam (sleutel ``HUB_…`` of ``{name: "HUB_…"}``)."""
    if isinstance(value, dict):
        name = value.get("name")
        if isinstance(name, str) and _HUB_RESERVED.match(name):
            yield f"{path}.name"
        for key, item in value.items():
            sub = f"{path}.{key}"
            if isinstance(key, str) and _HUB_RESERVED.match(key):
                yield sub
            yield from _hub_names_in_values(item, sub)
    elif isinstance(value, list):
        for idx, item in enumerate(value):
            yield from _hub_names_in_values(item, f"{path}[{idx}]")


def check_manifest(manifest: Manifest) -> list[Finding]:
    """Alle semantische regels op een (schema-geldig) manifest."""
    return _Rules(manifest).run()
