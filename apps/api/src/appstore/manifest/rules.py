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
import json
import re
import unicodedata
from collections.abc import Iterable, Iterator
from functools import lru_cache
from typing import Any, NamedTuple

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
#: Strikt koppelpad in de container, gelijk aan de Hub (KLEIN-82; ai-server-manager ``asm_policy.params``,
#: ``CONTAINERPAD_RE``): "/" gevolgd door delen van ASCII-letters, cijfers en ``. _ @ + -``, gescheiden door één "/";
#: geen "/" aan het eind. TrueNAS plakt de tmpfs-opties als ``"<mount_path>:<opties>"`` achter het pad, dus ":" en ","
#: mogen er nooit in. Altijd met ``fullmatch``.
CONTAINERPAD_RE = re.compile(r"/[A-Za-z0-9._@+-]+(/[A-Za-z0-9._@+-]+)*")
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
    """Controleer een koppelpad in de container (``storage[].mount`` en ``mount_path``/``mountPath`` in de waarden).

    Gelijk aan de Hub (KLEIN-82; ai-server-manager ``asm_policy.params``: ``CONTAINERPAD_RE`` en
    ``_mount_path_problem``): geen ``.``- of ``..``-deel (:func:`normalize_abs_path`), geen runtime-socket, geen
    :data:`FORBIDDEN_MOUNT_PATHS` of iets onder ``/proc``, ``/sys`` of ``/dev`` (strenger dan de Hub), en alleen een
    strikt containerpad (:data:`CONTAINERPAD_RE`).
    """
    norm = normalize_abs_path(path)
    if norm is None:
        return f"Koppelpad {path!r} is geen genormaliseerd absoluut pad."
    if any(marker in norm.lower() for marker in _SOCKET_MARKERS):
        return f"Koppelpad {path!r} is een container-runtime-socket."
    if norm in FORBIDDEN_MOUNT_PATHS or norm.startswith(("/proc/", "/sys/", "/dev/")):
        return f"Koppelpad {norm!r} overschrijft een systeemmap in de container."
    if not CONTAINERPAD_RE.fullmatch(path):
        return (
            f"Koppelpad {path!r} is geen strikt containerpad: '/' gevolgd door delen van letters, cijfers en "
            "'. _ @ + -' (ASCII), gescheiden door één '/', zonder '/' aan het eind."
        )
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

    def leaf(value: str, path: str, key: str, place: Place) -> None:
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
        if not resolved and place.secret and has_value(value):
            out.append(
                error("secret_literal_forbidden", path, "Geheim als letterlijke waarde in de catalogus-waarden.")
            )

    def visit(value: Any, path: str, key: str, place: Place) -> None:
        """``place``: de plek met wat zij van haar voorouders erft (ADR-0018: wat onder een geheimnaam ligt, erft hem)."""
        if isinstance(value, dict):
            if not resolved and set(value) == {"param"} and isinstance(value["param"], str):
                return
            # ADR-0016 W2: de naam van een naam/waarde-paar (``{name, value}``) geldt voor de waarde ernaast
            pair_name = name_value_pair(value)
            for k, sub in value.items():
                sub_path = f"{path}.{shown_key(str(k))}"
                nk = _nk(k)
                if pair_name is not None and k == "value":
                    visit(sub, sub_path, nk, place.child(pair_name))
                    continue
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
                                f"Het veld {shown_key(str(k))!r} (host-namespaces, beveiligingsprofielen, capabilities, "
                                "sysctls of eigen opdrachten) is in catalogus-waarden verboden.",
                            )
                        )
                else:
                    visit(sub, sub_path, nk, place.child(str(k)))
        elif isinstance(value, list):
            for idx, sub in enumerate(value):
                visit(sub, f"{path}[{idx}]", key, place)
        elif isinstance(value, str):
            leaf(value, path, key, place)
        elif not resolved and isinstance(value, int | float) and has_value(value) and place.secret:
            out.append(
                error("secret_literal_forbidden", path, "Geheim als letterlijke waarde in de catalogus-waarden.")
            )

    visit(values, "values", "", Place())
    return out


def _param_refs(
    value: Any, path: str, key: str = "", place: Place | None = None
) -> Iterator[tuple[str, str, str, Place]]:
    """Alle ``{"param": "<naam>"}``-verwijzingen in vrije data (catalogus-``values``), met de genormaliseerde sleutel en
    de plek (ADR-0016 W2, ADR-0018): de sleutel zoals hij er staat, of bij de ``value`` van een naam/waarde-paar de
    tekst onder ``name``, met wat de plek van haar voorouders erft. Het pad toont een sleutel met een waarde niet."""
    place = place if place is not None else Place()
    if isinstance(value, dict):
        if set(value) == {"param"} and isinstance(value["param"], str):
            yield path, value["param"], key, place
            return
        pair_name = name_value_pair(value)
        for sub_key, sub in value.items():
            pair = pair_name is not None and sub_key == "value"
            name = pair_name if pair and pair_name is not None else str(sub_key)
            yield from _param_refs(
                sub, f"{path}.{shown_key(str(sub_key))}", _nk(sub_key), place.child(name, whole=pair)
            )
    elif isinstance(value, list):
        for idx, sub in enumerate(value):
            yield from _param_refs(sub, f"{path}[{idx}]", key, place)


# ---------------------------------------------------------------------------------------------- geheimen (ADR-0016 W2)
#: Woorden die van een naam een geheimnaam maken (ADR-0016 W2, "Begrippen"): de woorden van de vroegere regel
#: ``_SECRET_ENV`` (die alleen het eind van de naam bekeek) plus ``WACHTWOORD``. Elk woord telt op elke plek in de naam.
SECRET_WORDS: tuple[str, ...] = (
    "PASS",
    "PASSWD",
    "PASSWORD",
    "SECRET",
    "TOKEN",
    "API_KEY",
    "APIKEY",
    "PRIVATE_KEY",
    "PRIVATEKEY",
    "CREDENTIAL",
    "AUTH_KEY",
    "SALT",
    "WACHTWOORD",
)
#: Woorden die van een geheimnaam een accountnaam maken (vaste lijst; een uitbreiding is een wijziging van de standaard).
#: ADR-0020 (besluit BN-1 van de regisseur, aanvulling op ADR-0019) voegt de samenstellingen vanaf ``ADMINISTRATOR`` toe:
#: als heel naamdeel in een voorouder tellen ze dan weer (``administrator``, ``beheerders``, ``rootuser``). De lijst
#: blijft open: een nieuwe samenstelling (``sysadmin``) telt pas na een nieuwe uitbreiding. ADR-0023 (contract §9,
#: aanvulling C-12; besluit BN-490-1) voegt ``INIT``, ``LOGIN`` en ``BASIC_AUTH`` toe.
ACCOUNT_WORDS: tuple[str, ...] = (
    "BOOTSTRAP",
    "DEFAULTUSER",
    "DEFAULT_USER",
    "ADMIN",
    "ROOT",
    "SUPERUSER",
    "OWNER",
    "INITIAL",
    "SETUP",
    "BEHEER",
    "SERVICE_ACCOUNT",
    "SERVICEACCOUNT",
    "DIENST",
    "ADMINISTRATOR",
    "BEHEERDER",
    "DIENSTACCOUNT",
    "BEHEERACCOUNT",
    "SUPERADMIN",
    "ROOTUSER",
    "ADMINUSER",
    "INIT",
    "LOGIN",
    "BASIC_AUTH",
)
#: ADR-0023 (contract §9, aanvulling C-12; besluit BN-490-1): aanmeldnamen van een app die alleen als **hele naam** een
#: accountnaam zijn: de naam van een omgevingsvariabele, van een naam/waarde-paar of van een parameter. Een sleutel in
#: ``values`` en het laatste deel van een naam met punten tellen niet (vervolgpunt; gemeten: 0 van 22 bestanden).
LOGIN_NAMES: frozenset[str] = frozenset({"JUPYTER_TOKEN", "PASSWORD"})
#: Sleutels van een gebruikerslijst: ``users`` (users.js van Node-RED) en de Nederlandse vorm.
USER_LIST_KEYS = frozenset({"users", "gebruikers"})
_KEY_PARTS = re.compile(r"[.\[\]]+")

#: T1: een wachtwoordhash. bcrypt in elke vorm die de bcrypt van Node-RED (``@node-rs/bcrypt``, ``users.js``) aanneemt
#: (ADR-0017: ook ``$2x$``, zonder ``$`` vooraan, met dubbele ``$`` en met kosten als ``+10`` of ``010``), plus de
#: PHC-vormen ``$argon2…``, ``$scrypt$`` en ``$pbkdf2…`` (ADR-0016 W2). De ``$``-tekens vóór ``2`` staan niet in het
#: patroon: ``search`` vindt dezelfde hashes, maar zonder ``\$*`` vooraan in lineaire tijd (ADR-0018).
_BCRYPT = re.compile(r"2[abxy]\$+\+?[0-9]+\$+[./A-Za-z0-9]{53}")
_PHC = re.compile(r"\$(?:argon2|scrypt\$|pbkdf2)")
#: JSON-escapes zoals ``JSON.parse`` ze oplost (``\u0024`` → ``$``, ``\/`` → ``/``).
_JSON_ESCAPE = re.compile(r"\\(?:u([0-9A-Fa-f]{4})|([\"\\/bfnrt]))")
_SIMPLE_ESCAPES = {'"': '"', "\\": "\\", "/": "/", "b": "\b", "f": "\f", "n": "\n", "r": "\r", "t": "\t"}
#: T2 stap 1: woorden zoals bash ``$FLOWS`` ongequoot splitst (standaard-IFS: spatie, tab, regeleinde).
_IFS = re.compile(r"[ \t\n]+")
#: Een define-waarde in de vorm ``sleutel=waarde`` (zoals ``red.js``: ``/^(([^=]+)=(.+)|@(.*))$/``).
_SETTING = re.compile(r"([^=]+)=(.+)", re.DOTALL)
#: ADR-0017: een kruisverwijzing in de sleutel van een instelling (``[msg.…]``, ``[msg[…]]``; ``normalisePropertyExpression``
#: van Node-RED) en een index (``[0]``, ``["0"]``, ``.0``).
_XREF = re.compile(r"\[\s*msg\s*[.\[]", re.IGNORECASE)
_INDEX = re.compile(r"\[\s*[\"']?[0-9]+[\"']?\s*\]|\.[0-9]+(?=[.\[]|$)")
#: T2 stap 2 (vangnet): een ``naam=`` met waarde en het begin van een pad met ``users``/``gebruikers``. Het vangnet
#: stopt alleen op de IFS-witruimte van bash (spatie, tab, regeleinde): U+00A0 of een stuurteken na de ``=`` is een
#: waarde (ADR-0018). JSON-sleutels en paden worden in lineaire tijd gelezen (:func:`_json_keys`, :func:`_users_path`).
_NAME_ASSIGN = re.compile(r"(?<![A-Za-z0-9_])([A-Za-z0-9_]+)[\"'\]]*=(?=[^ \t\n])")
_USERS_PREFIX = re.compile(r"(?<![A-Za-z0-9_])[\"']?(?:users|gebruikers)[\"']?\]?[.\[]", re.IGNORECASE)
_PATH_STOP = re.compile(r"[= \t\n]")
#: Witruimte tussen een JSON-sleutel en de ``:`` (elke witruimte telt: meer sleutels, fail-closed), en JSON-witruimte
#: na de ``:`` en binnen een lijst of object (alleen die slaat ``JSON.parse`` over).
_ANY_WS = re.compile(r"\s*")
_JSON_WS = re.compile(r"[ \t\n\r]*")
_IFS_CHARS = " \t\n"
#: Alles wat op een vault-verwijzing lijkt (ook met hoofdletters of midden in een tekst). Een manifest bevat er nooit
#: een (contract §3.1, ADR-0023 besluit 3); alleen de vertaling zet een verwijzing.
_VAULT_MARK = re.compile(r"vault://", re.IGNORECASE)
#: ADR-0018: een tekst die langer is dan dit, toetsen T1 en T2 niet; hij is fail-closed een fout.
MAX_SECRET_TEXT = 64 * 1024
#: ADR-0018: hoe een sleutel in een pad of melding staat als hij een ``=`` heeft of als T1/T2 hem herkent.
HIDDEN_KEY = "<verborgen>"

_R_HASH = "een wachtwoordhash (bcrypt of PHC-vorm)"
_R_GEHEIMNAAM = "een sleutel met een geheimnaam en een waarde"
_R_USERS = "een gebruikerslijst (users of gebruikers) met inhoud"
_R_FILE = "instellingen uit een bestand (@), buiten het zicht van de validator"
_R_XREF = "een kruisverwijzing ([msg.…]) of een index in de sleutel van een instelling"
_R_TE_LANG = f"een tekst van meer dan {MAX_SECRET_TEXT} tekens, te lang om op geheimen te toetsen (fail-closed)"


def _last_part(name: str) -> str:
    """Bij een sleutel met punten of ``[n]`` telt het laatste deel (``adminAuth.users[0].password`` → ``password``)."""
    parts = [p.strip().strip("\"'") for p in _KEY_PARTS.split(name)]
    parts = [p for p in parts if p]
    return parts[-1] if parts else ""


#: Per woord: elke plek waar het woord (hoofdletterongevoelig) begint, ook overlappend.
_SECRET_WORD_AT = tuple((word, re.compile(f"(?={re.escape(word)})", re.IGNORECASE)) for word in SECRET_WORDS)
_ACCOUNT_WORD_AT = tuple((word, re.compile(f"(?={re.escape(word)})", re.IGNORECASE)) for word in ACCOUNT_WORDS)


#: ADR-0019: alleen een naam van hooguit zoveel tekens gaat in de cache van :func:`is_secret_name` en :func:`shown_key`
#: (elk hooguit 4096 namen); een langere naam wordt door :func:`is_secret_name` direct getoetst. Een cache op tekst van
#: willekeurige lengte hield elke sleutel uit een verzoek vast, tot honderden MiB, tot de dienst herstartte. Elke
#: doorloop toetst elke naam één keer (de getoonde naam van een plek gaat mee), zodat ook een lange naam lineair blijft.
CACHED_NAME_MAX = 256
#: ADR-0021: een sleutel van meer dan zoveel tekens staat in een pad of melding in één vaste vorm, :data:`HIDDEN_KEY`
#: (zie :func:`shown_key`). Zo draagt geen melding een sleutel van tienduizenden tekens (één verzoek gaf een antwoord
#: van honderden MiB), en is het tonen van een lange sleutel geen rekenwerk. Gelijk aan :data:`CACHED_NAME_MAX`: elke
#: sleutel die wel getoond wordt, past in de cache. De regels zelf (geheimnaam, accountwoord, T1) lezen de hele sleutel.
SHOWN_KEY_MAX = CACHED_NAME_MAX


def is_secret_name(name: str) -> bool:
    """Geheimnaam (ADR-0016 W2, ADR-0018); een korte naam komt uit de cache (ADR-0019, :data:`CACHED_NAME_MAX`)."""
    return _is_secret_name_cached(name) if len(name) <= CACHED_NAME_MAX else _is_secret_name(name)


@lru_cache(maxsize=4096)
def _is_secret_name_cached(name: str) -> bool:
    return _is_secret_name(name)


def _is_secret_name(name: str) -> bool:
    """Geheimnaam (ADR-0016 W2, ADR-0018): een van :data:`SECRET_WORDS` staat in de naam, gevolgd door het einde,
    ``_``, ``-``, ``.`` of een cijfer (``DB_PASSWORD2``, ``ADMIN_TOKEN1``), hoofdletterongevoelig, of door een
    hoofdletter na een kleine letter (camelCase: ``secretKey``, ``passwordHash``, ``apiKeyValue``; niet
    ``SECRETS_DIR``, ``TOKENS`` of ``PASSWORDLESS``). Het stuk tekst heeft precies de lengte van het woord: een teken
    dat met hoofdletters langer wordt (``ß`` → ``SS``) maakt er geen geheimwoord van (``paß``, ``Reisepaß``)."""
    part = _last_part(name)
    for word, at in _SECRET_WORD_AT:
        for m in at.finditer(part):
            start = m.start()
            end = start + len(word)
            if end > len(part) or part[start:end].upper() != word:
                continue
            if end == len(part) or part[end] in "_-." or part[end].isdigit():
                return True
            if part[end].isupper() and part[end - 1].islower():
                return True
    return False


def _part_edge(name: str, i: int) -> bool:
    """Ligt op positie ``i`` een grens tussen twee naamdelen (ADR-0019)? Het begin en het einde van de naam, naast elk
    teken dat geen letter of cijfer is (``_``, ``-``, ``.``, spatie), tussen een letter en een cijfer, op een
    camelCase-grens (``adminUser``) en vóór de laatste hoofdletter van een reeks hoofdletters (``ADMINUser``)."""
    if i <= 0 or i >= len(name):
        return True
    before, after = name[i - 1], name[i]
    if not before.isalnum() or not after.isalnum() or before.isdigit() != after.isdigit():
        return True
    if before.islower() and after.isupper():
        return True
    return before.isupper() and after.isupper() and i + 1 < len(name) and name[i + 1].islower()


def has_account_word(name: str) -> bool:
    """Staat een van :data:`ACCOUNT_WORDS` als heel naamdeel in de naam (ADR-0019, keuze van de regisseur)? Het woord
    begint en eindigt op een grens tussen naamdelen (:func:`_part_edge`), eventueel met een meervouds-``s``:
    ``admin``, ``Admin_ui``, ``adminUser``, ``root user``, ``admins``, ``admin2`` wel; ``phpmyadmin``, ``rootless``,
    ``groot``, ``administration`` niet. Hoofdletterongevoelig, zonder lengteverschil (zoals :func:`is_secret_name`)."""
    for word, at in _ACCOUNT_WORD_AT:
        for m in at.finditer(name):
            start = m.start()
            end = start + len(word)
            if end > len(name) or name[start:end].upper() != word or not _part_edge(name, start):
                continue
            if _part_edge(name, end) or (end < len(name) and name[end] in "sS" and _part_edge(name, end + 1)):
                return True
    return False


def _account_word_inside(name: str) -> bool:
    """Staat een van :data:`ACCOUNT_WORDS` ergens in de naam, ook midden in een woord (hoofdletterongevoelig)?"""
    upper = name.upper()
    return any(word in upper for word in ACCOUNT_WORDS)


def is_account_name(name: str) -> bool:
    """Accountnaam (ADR-0016 W2): een geheimnaam waarin ook een van :data:`ACCOUNT_WORDS` staat, ergens in de naam
    (``ADMINISTRATOR_PASSWORD``, ``beheerwachtwoord``); bij een sleutel met punten of ``[n]`` in het laatste deel, zoals
    bij een geheimnaam (ADR-0021: ``phpmyadmin.password`` is geen accountnaam). ADR-0019 versmalt dit niet: alleen een
    accountwoord in een voorouder of in een naam zonder geheimwoord moet een heel naamdeel zijn
    (:func:`has_account_word`). ADR-0023: ook een aanmeldnaam uit :data:`LOGIN_NAMES` als hele naam; roep dit alleen aan
    met een hele naam (omgevingsvariabele of parameter), nooit met een sleutel in ``values``."""
    return is_secret_name(name) and (_account_word_inside(_last_part(name)) or is_login_name(name))


def is_login_name(name: str) -> bool:
    """Is de hele naam een aanmeldnaam uit :data:`LOGIN_NAMES` (ADR-0023)? Hoofdletterongevoelig, zonder lengteverschil
    (zoals :func:`is_secret_name`)."""
    upper = name.upper()
    return len(upper) == len(name) and upper in LOGIN_NAMES


def param_name_counts(param: Parameter) -> bool:
    """Gelden de naamregels voor de naam van deze parameter? Niet voor type ``boolean`` zonder geheimmarkering
    (ADR-0019; uitleg (a): een geheim is nooit een boolean). Een boolean die zelf als geheim is gemarkeerd
    (:func:`is_marked_secret`) valt er wel onder (ADR-0021): het manifest noemt hem een geheim. De regels voor de plek
    zelf gelden altijd."""
    return param.type != "boolean" or is_marked_secret(param)


class Place(NamedTuple):
    """Een plek in ``values`` met wat zij van haar voorouders erft (ADR-0018). Een plek is een **geheimplek** als haar
    eigen naam of de naam van een voorouder een geheimnaam is (wat onder een geheimnaam ligt, erft die naam: een object
    net als een lijst), en een **accountplek** als zij een geheimplek is en haar eigen naam een accountnaam is of haar
    eigen naam of een voorouder een accountwoord als heel naamdeel heeft (``admin.password``, ``adminUser.password``,
    ``admin_password.x``; ADR-0019). De naam is een sleutel, of bij de ``value`` van een naam/waarde-paar de tekst
    onder ``name``. Elke naam wordt één keer getoetst (lineair, ook voor een naam die te lang is voor de cache;
    ADR-0019); een naam van meer dan :data:`SHOWN_KEY_MAX` tekens wordt nooit getoond (ADR-0021). ``whole``: de naam is
    de hele naam van een omgevingsvariabele (de ``name`` van een naam/waarde-paar of van ``values.envs``); dan telt ook
    een aanmeldnaam uit :data:`LOGIN_NAMES` (ADR-0023), bij een sleutel niet."""

    #: de eigen naam van de plek
    name: str = ""
    #: de naam die haar een geheimplek maakt (de eigen naam of die van de dichtstbijzijnde voorouder), of ``""``
    secret_name: str = ""
    #: de eigen naam of een voorouder heeft een accountwoord (ADR-0019: als heel naamdeel, of een accountnaam)
    account_word: bool = False
    #: ``secret_name`` zoals hij in een melding staat (:func:`shown_key`), één keer berekend
    shown_secret: str = ""

    def child(self, name: str, *, whole: bool = False) -> Place:
        secret = is_secret_name(name)
        return Place(
            name,
            name if secret else self.secret_name,
            self.account_word
            or has_account_word(name)
            or (secret and _account_word_inside(_last_part(name)))
            or (whole and is_login_name(name)),
            shown_key(name) if secret else self.shown_secret,
        )

    @property
    def secret(self) -> bool:
        return self.secret_name != ""

    @property
    def account(self) -> bool:
        return self.secret and self.account_word


def shown_key(key: str) -> str:
    """Een sleutel zoals hij in een pad of melding staat. Met een ``=`` of met iets dat T1 of T2 herkent, staat er
    :data:`HIDDEN_KEY`, want zo'n sleutel kan zelf een waarde dragen (ADR-0018). Een sleutel van meer dan
    :data:`SHOWN_KEY_MAX` tekens staat er ook als :data:`HIDDEN_KEY` (ADR-0021: één vaste vorm, zonder de sleutel te
    lezen). Elke andere sleutel komt uit de cache (ADR-0019)."""
    return _shown_key_cached(key) if len(key) <= SHOWN_KEY_MAX else HIDDEN_KEY


@lru_cache(maxsize=4096)
def _shown_key_cached(key: str) -> str:
    return HIDDEN_KEY if "=" in key or secret_text_reasons(key, settings=True) else key


def is_marked_secret(param: Parameter) -> bool:
    """Parameter met een geheimmarkering (``secret: true``, ``type: secret_ref`` of ``source: secret``/``generated``);
    een parameter zonder markering is een gewone parameter (ADR-0016 W2)."""
    return param.secret or param.type == "secret_ref" or param.source in ("secret", "generated")


def marked_account_param(param: Parameter) -> bool:
    """ADR-0022 (besluit B-2, volgt uit C-12 "strikt zonder"): een parameter met een geheimmarkering
    (:func:`is_marked_secret`) waarvan de eigen naam een accountwoord als heel naamdeel heeft (:func:`has_account_word`:
    ``admin_pw``, ``root_key``, ``beheer_code``) is de aanmelding van een beheerder of dienstaccount, net als een
    parameter met een accountnaam: ``account_gegevens_verboden`` op elke plek, ook op een plek zonder accountwoord.
    Een accountwoord als deel van een woord telt niet (``phpmyadmin_pw``), en een gewone parameter zonder
    geheimmarkering ook niet."""
    return is_marked_secret(param) and has_account_word(param.name)


def has_value(value: Any) -> bool:
    """Niet-lege waarde (ADR-0016 W2, ADR-0018): elke tekst behalve de lege tekst (ook alleen spaties, U+00A0 of
    stuurtekens: bash, ``nopt`` en de apps zien die als waarde), een getal of een niet-lege lijst of object. Ja/nee en
    ``null`` tellen niet: een geheim is nooit een boolean."""
    if value is None or isinstance(value, bool):
        return False
    if isinstance(value, str):
        return value != ""
    if isinstance(value, list | dict):
        return len(value) > 0
    return True


def name_value_pair(value: dict[Any, Any]) -> str | None:
    """De naam van een naam/waarde-paar in ``values`` (een object met ``name`` en ``value``, zoals
    ``additional_envs`` van Authentik), of ``None``."""
    name = value.get("name")
    return name if isinstance(name, str) and "value" in value else None


def has_password_hash(text: str) -> bool:
    """T1: een wachtwoordhash in de tekst zoals hij er staat (zie :data:`_BCRYPT` en :data:`_PHC`)."""
    return bool(_BCRYPT.search(text) or _PHC.search(text))


def _unescape(text: str) -> str:
    def repl(m: re.Match[str]) -> str:
        return chr(int(m.group(1), 16)) if m.group(1) else _SIMPLE_ESCAPES[m.group(2)]

    return _JSON_ESCAPE.sub(repl, text)


def _json_or_text(raw: str) -> Any:
    """Zoals ``red.js``: de waarde gaat door ``JSON.parse``; lukt dat niet, dan blijft het tekst."""
    try:
        return json.loads(raw)
    except (ValueError, RecursionError):
        return raw


def _decoded_reasons(value: Any) -> list[str]:
    """T2 stap 1 op een gedecodeerde waarde: geheimnaam met waarde, gebruikerslijst met inhoud of een hash (T1)."""
    reasons: list[str] = []
    stack = [value]
    while stack:
        item = stack.pop()
        if isinstance(item, dict):
            for key, sub in item.items():
                text = str(key)
                if is_secret_name(text) and has_value(sub):
                    reasons.append(_R_GEHEIMNAAM)
                if _last_part(text).lower() in USER_LIST_KEYS and isinstance(sub, list | dict) and sub:
                    reasons.append(_R_USERS)
                if has_password_hash(text):
                    reasons.append(_R_HASH)
                stack.append(sub)
        elif isinstance(item, list):
            stack.extend(item)
        elif isinstance(item, str) and has_password_hash(item):
            reasons.append(_R_HASH)
    return reasons


def t2_setting_reasons(text: str) -> list[str]:
    """T2 stap 1 (ADR-0016 W2): de tekst splitsen zoals bash en elk woord decoderen zoals Node-RED, zonder optielijst.

    Het deel na de eerste ``=`` van een woord met ``-`` vooraan, en elk woord zonder ``-`` vooraan met een ``=``, telt
    als instelling (ook een los woord: fail-closed). Een instelling of woord dat met ``@`` begint, leest een bestand.
    Een woord zonder ``-`` en ``=`` dat met ``{`` of ``[`` begint, wordt als JSON gelezen. ADR-0017: een kruisverwijzing
    of index in de sleutel is ook een fout. Geeft de redenen, nooit de waarde."""
    reasons: list[str] = []
    for word in _IFS.split(text):
        if not word:
            continue
        candidate: str | None = None
        if word.startswith("-"):
            if "=" in word:
                candidate = word.split("=", 1)[1]
                if candidate.startswith("@"):
                    reasons.append(_R_FILE)
                    continue
        elif word.startswith("@"):
            reasons.append(_R_FILE)
            continue
        elif "=" in word:
            candidate = word
        elif word[0] in "{[":
            reasons.extend(_decoded_reasons(_json_or_text(word)))
            continue
        setting = _SETTING.fullmatch(candidate) if candidate is not None else None
        if setting is None:
            continue
        key, value = setting.group(1), _json_or_text(setting.group(2))
        if _XREF.search(key) or _INDEX.search(key):
            reasons.append(_R_XREF)
        if is_secret_name(key) and has_value(value):
            reasons.append(_R_GEHEIMNAAM)
        if _last_part(key).lower() in USER_LIST_KEYS and isinstance(value, list | dict) and value:
            reasons.append(_R_USERS)
        if has_password_hash(key):
            reasons.append(_R_HASH)
        reasons.extend(_decoded_reasons(value))
    return reasons


def _closing_quotes(text: str) -> list[int]:
    """Per positie ``p``: de positie van de ``"`` die een JSON-tekst sluit waarvan de inhoud op ``p`` begint, of -1.

    Hetzelfde als ``"((?:[^"\\]|\\.)*)"`` vanaf elke ``"`` (een ``\\`` slaat het volgende teken over, behalve een
    regeleinde), maar één keer van rechts naar links gelezen: lineair, ook als geen enkele tekst sluit (ADR-0018)."""
    n = len(text)
    close = [-1] * (n + 2)
    for p in range(n - 1, -1, -1):
        ch = text[p]
        if ch == '"':
            close[p] = p
        elif ch == "\\":
            close[p] = close[p + 2] if p + 1 < n and text[p + 1] != "\n" else -1
        else:
            close[p] = close[p + 1]
    return close


def _skip(pattern: re.Pattern[str], text: str, pos: int) -> int:
    """Positie na de witruimte die ``pattern`` vanaf ``pos`` vindt (het patroon kan leeg zijn)."""
    m = pattern.match(text, pos)
    return m.end() if m else pos


def _json_keys(text: str, close: list[int]) -> Iterator[tuple[str, int]]:
    """Elke JSON-sleutel ``"naam":`` in de tekst, met de positie van de waarde erna, in dezelfde volgorde als een
    zoektocht van links naar rechts die bij elke ``"`` opnieuw begint (ook binnen een andere tekst: fail-closed), in
    lineaire tijd."""
    n = len(text)
    after: dict[int, int] = {}
    pos = 0
    while (start := text.find('"', pos)) >= 0:
        end = close[start + 1]
        if end >= 0 and end not in after:
            colon = _skip(_ANY_WS, text, end + 1)
            after[end] = _skip(_JSON_WS, text, colon + 1) if colon < n and text[colon] == ":" else -1
        value_at = after.get(end, -1) if end >= 0 else -1
        if value_at < 0:
            pos = start + 1
            continue
        yield text[start + 1 : end], value_at
        pos = value_at


def _json_value_follows(text: str, pos: int, close: list[int], *, containers_only: bool) -> bool:
    """Staat er na een JSON-sleutel een niet-lege waarde (alleen lijst/object bij ``containers_only``)? Een tekst
    telt zodra zij niet leeg is (ADR-0018: niet strippen); een tekst die niet sluit, telt ook (fail-closed)."""
    if pos >= len(text):
        return False
    rest = text[pos]
    if rest in "[{":
        inner = _skip(_JSON_WS, text, pos + 1)
        return inner < len(text) and text[inner] != ("]" if rest == "[" else "}")
    if containers_only:
        return False
    if rest == '"':
        end = close[pos + 1]
        return end < 0 or end > pos + 1
    if text.startswith(("true", "false", "null"), pos):
        return False
    return rest not in ",}]"


def _users_path(text: str) -> bool:
    """Een pad met een deel ``users``/``gebruikers`` (ook tussen aanhalingstekens of haken) gevolgd door ``[`` of
    ``.``, vóór een ``=`` met een waarde; het pad stopt bij de eerste ``=`` of IFS-witruimte. Lineair: elk begin van
    een pad hergebruikt de eerste stop die al gevonden is."""
    stop = -1
    for m in _USERS_PREFIX.finditer(text):
        if stop < m.end():
            found = _PATH_STOP.search(text, m.end())
            if found is None:
                return False
            stop = found.start()
        if text[stop] == "=" and stop + 1 < len(text) and text[stop + 1] not in _IFS_CHARS:
            return True
    return False


def t2_safety_net_reasons(text: str) -> list[str]:
    """T2 stap 2, het vangnet (ADR-0016 W2): los van stap 1, op de tekst zelf én op dezelfde tekst met alle
    JSON-escapes opgelost. Een JSON-sleutel met een geheimnaam en een waarde, een JSON-sleutel ``users``/``gebruikers``
    met inhoud, een ``naam=`` met waarde waarin ``naam`` een geheimnaam is, een pad met een deel ``users`` of
    ``gebruikers`` vóór een ``=`` met waarde, en een hash volgens T1. Alles in lineaire tijd (ADR-0018)."""
    reasons: list[str] = []
    for variant in dict.fromkeys((text, _unescape(text))):
        if '"' in variant:
            close = _closing_quotes(variant)
            for raw_key, value_at in _json_keys(variant, close):
                key = _unescape(raw_key)
                if is_secret_name(key) and _json_value_follows(variant, value_at, close, containers_only=False):
                    reasons.append(_R_GEHEIMNAAM)
                if _last_part(key).lower() in USER_LIST_KEYS and _json_value_follows(
                    variant, value_at, close, containers_only=True
                ):
                    reasons.append(_R_USERS)
        if any(is_secret_name(m.group(1)) for m in _NAME_ASSIGN.finditer(variant)):
            reasons.append(_R_GEHEIMNAAM)
        if _users_path(variant):
            reasons.append(_R_USERS)
        if has_password_hash(variant):
            reasons.append(_R_HASH)
    return reasons


def secret_text_reasons(text: str, *, settings: bool) -> list[str]:
    """Tekstregels van ADR-0016 W2: T1 op elke tekst; met ``settings`` ook T2 (stap 1 en het vangnet). Elke reden
    één keer, in vaste volgorde. Een tekst langer dan :data:`MAX_SECRET_TEXT` wordt niet getoetst maar geweigerd
    (ADR-0018, fail-closed)."""
    if len(text) > MAX_SECRET_TEXT:
        return [_R_TE_LANG]
    reasons = t2_setting_reasons(text) + t2_safety_net_reasons(text) if settings else []
    if has_password_hash(text):
        reasons.append(_R_HASH)
    return list(dict.fromkeys(reasons))


def secret_text_finding(path: str, reasons: list[str]) -> Finding:
    return error(
        "account_gegevens_verboden",
        path,
        "Geheim of accountgegevens in een tekst: "
        + "; ".join(reasons)
        + ". Een wachtwoord, wachtwoordhash, geheime sleutel of gebruikerslijst hoort nooit in een manifest of in het "
        "Custom App-formulier; een beheerder of dienstaccount komt er via de app zelf (ADR-0016).",
    )


def render_shown(segments: tuple[str | int, ...]) -> str:
    """Pad voor een melding, bv. ``values.voorbeeld.x``; een sleutel die een waarde kan dragen of te lang is, staat er
    als :data:`HIDDEN_KEY` (:func:`shown_key`, ADR-0018, ADR-0021)."""
    out = ""
    for s in segments:
        out += f"[{s}]" if isinstance(s, int) else (f".{shown_key(s)}" if out else shown_key(s))
    return out


class PathNode(NamedTuple):
    """Eén stap van een pad als gelinkte keten (ADR-0021): de ouder en het eigen segment. Een doorloop zet zo per
    knooppunt alleen deze twee verwijzingen op de stapel, geen kopie van het hele pad; de tekst van het pad
    (:func:`shown_path`) en de plek als reeks (:func:`path_segments`) worden pas gemaakt als ze nodig zijn (bij een
    melding of bij het opzoeken van een plek). ``None`` is de wortel."""

    parent: PathNode | None
    #: een sleutel van een object, of de positie in een lijst
    segment: Any
    #: ``True`` voor een positie in een lijst (``[3]``), ``False`` voor een sleutel (``.naam``)
    in_list: bool = False


def path_segments(node: PathNode | None) -> tuple[Any, ...]:
    """De plek als reeks sleutels en lijstposities, van de wortel naar ``node``."""
    out: list[Any] = []
    while node is not None:
        out.append(node.segment)
        node = node.parent
    out.reverse()
    return tuple(out)


def shown_path(node: PathNode | None) -> str:
    """Pad voor een melding, zoals :func:`render_shown`: elke sleutel via :func:`shown_key`. Alleen aanroepen als er
    een melding is (ADR-0021); het pad is dan hooguit zo lang als de diepte keer :data:`SHOWN_KEY_MAX`."""
    chain: list[PathNode] = []
    while node is not None:
        chain.append(node)
        node = node.parent
    parts: list[str] = []
    filled = False  # staat er al tekst? Dan komt er een punt vóór een sleutel (zoals in :func:`render_shown`)
    for step in reversed(chain):
        if step.in_list:
            part = f"[{step.segment}]"
        else:
            key = shown_key(str(step.segment))
            part = f".{key}" if filled else key
        filled = filled or part != ""
        parts.append(part)
    return "".join(parts)


def _settings_place(segments: tuple[str | int, ...]) -> bool:
    """Waar ook T2 geldt: ``custom_app.env[].value``, elke tekst in ``values`` en ``default``/``example`` van een
    parameter (ADR-0016 W2)."""
    if segments[:1] == ("values",):
        return True
    if len(segments) == 4 and segments[:2] == ("custom_app", "env") and segments[3] == "value":
        return True
    return len(segments) == 3 and segments[0] == "parameters" and segments[2] in ("default", "example")


def _text_children(
    value: dict[Any, Any] | list[Any], node: PathNode | None, depth: int, in_values: bool
) -> Iterator[tuple[Any, PathNode, int, bool]]:
    """De kinderen van een object of lijst, van achter naar voren (de volgorde waarin :func:`secret_text_findings` ze
    altijd al las), elk met een eigen :class:`PathNode`. Lui: er staat nooit meer dan één kind per niveau klaar."""
    if isinstance(value, dict):
        for key, sub in reversed(value.items()):
            yield sub, PathNode(node, str(key)), depth + 1, in_values or (depth == 0 and str(key) == "values")
    else:
        for idx in range(len(value) - 1, -1, -1):
            yield value[idx], PathNode(node, idx, True), depth + 1, in_values


def vault_ref_finding(path: str, *, key: bool) -> Finding:
    """ADR-0023 besluit 3: een kluisverwijzing in het manifest; de melding noemt het pad, nooit de tekst."""
    return error(
        "vault_ref_not_allowed",
        path,
        ("In een sleutel van dit object staat" if key else "Hier staat")
        + " een kluisverwijzing (vault://). Een manifest noemt alleen de parameter; de verwijzing zet alleen de "
        "vertaling, voor een geheime parameter van de eigen app (contract §3.1, ADR-0023).",
    )


def secret_text_findings(data: Any) -> list[Finding]:
    """Tekstregels T1 en T2 van ADR-0016 W2 (``account_gegevens_verboden``) op de ruwe gegevens, net als
    ``validator._compose_findings``, zodat de melding er ook is als het schema al faalt. Een sleutel valt onder de
    naamregels; alleen op een sleutel in ``values`` geldt daarnaast T1 (ADR-0018). Elke tekst geeft hooguit één
    melding, met het pad en de reden, nooit de waarde. ADR-0023 besluit 3: een kluisverwijzing in een tekst of in een
    sleutel in ``values`` geeft daarnaast ``vault_ref_not_allowed``. ADR-0021: de doorloop houdt per niveau één rij kinderen bij en
    per knooppunt alleen de ouder en het segment (:class:`PathNode`); het getoonde pad wordt pas bij een melding
    gemaakt. Geheugen en rekentijd hangen zo niet af van de lengte van het pad keer het aantal knooppunten."""
    out: list[Finding] = []
    first: list[tuple[Any, PathNode | None, int, bool]] = [(data, None, 0, False)]
    stack: list[Iterator[tuple[Any, PathNode | None, int, bool]]] = [iter(first)]
    while stack:
        item = next(stack[-1], None)
        if item is None:
            stack.pop()
            continue
        value, node, depth, in_values = item
        if isinstance(value, dict):
            if in_values:
                for key in value:
                    if reasons := secret_text_reasons(str(key), settings=False):
                        out.append(secret_text_finding(shown_path(PathNode(node, str(key))), reasons))
                    if _VAULT_MARK.search(str(key)):
                        out.append(vault_ref_finding(shown_path(node), key=True))
            stack.append(_text_children(value, node, depth, in_values))
        elif isinstance(value, list):
            stack.append(_text_children(value, node, depth, in_values))
        elif isinstance(value, str):
            # buiten ``values`` geldt T2 alleen op een plek van 3 of 4 segmenten diep (:func:`_settings_place`)
            settings = in_values or (depth in (3, 4) and _settings_place(path_segments(node)))
            if reasons := secret_text_reasons(value, settings=settings):
                out.append(secret_text_finding(shown_path(node), reasons))
            if _VAULT_MARK.search(value):
                out.append(vault_ref_finding(shown_path(node), key=False))
    return out


def recognised_secret(data: Any, segments: tuple[str | int, ...], instance: Any) -> bool:
    """Herkent W2 de waarde van een schemafout als geheim of accountgegeven? Dan noemt de melding haar niet (ADR-0016
    W2): onder ``accounts``, een geheimnaam als sleutel of als ``name`` op het pad, of een tekst waarin T1 of T2 iets
    vindt."""
    if segments[:1] == ("accounts",):
        return True
    node: Any = data
    for segment in segments:
        if isinstance(node, dict):
            name = node.get("name") if node is not data else None  # de naam van de app zelf telt niet
            if isinstance(name, str) and is_secret_name(name):
                return True
            if isinstance(segment, str) and is_secret_name(segment):
                return True
            node = node.get(segment)
        elif isinstance(node, list) and isinstance(segment, int) and 0 <= segment < len(node):
            node = node[segment]
        else:
            break
    texts: list[str] = []
    stack = [instance]
    while stack:
        item = stack.pop()
        if isinstance(item, dict):
            texts.extend(str(k) for k in item)
            stack.extend(item.values())
        elif isinstance(item, list):
            stack.extend(item)
        elif isinstance(item, str):
            texts.append(item)
    return any(secret_text_reasons(text, settings=True) for text in texts)


# ---------------------------------------------------------------------------------------------- regels
#: contract 1.3 §9: restrisico dat een upstream-image als Custom App in ``privilege_reason`` noemt
NNP_RISK_NL = "zonder no-new-privileges kan een setuid-programma binnen de container root worden"
_HUB_RESERVED = re.compile(r"^hub_", re.I)
#: ADR-0016 W1: de grens van ``validate_manifest_meta`` in het beleid van de Hub voor de tekst van een controle die een
#: account noemt (het schema laat 1000 toe). Vervalt zodra contract en beleid dezelfde grens hebben.
ACCOUNT_CHECK_MAX = 500


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

    def account_value(self, path: str, shown: str) -> None:
        """N3 (ADR-0016 W2, ADR-0018, ADR-0019, ADR-0021, ADR-0022): een accountplek krijgt haar waarde via
        ``value_from`` of ``{param}``, van welke parameter ook, of een parameter met een accountnaam wordt gebruikt, of een
        parameter met een geheimnaam of een geheimmarkering komt op een plek met een accountwoord, of een parameter met
        een geheimmarkering en een accountwoord in de eigen naam wordt gebruikt. Niet te accepteren; op dit pad geen
        ``secret_in_form``. ``shown`` is de naam zoals hij in de melding staat (:func:`shown_key`)."""
        self.err(
            "account_gegevens_verboden",
            path,
            f"{shown} is de aanmelding van een beheerder of dienstaccount; die komt nooit via het manifest of "
            "het Custom App-formulier, ook niet als geheim dat de Hub maakt. De beheerder zet het account in de app "
            "zelf (ADR-0016, besluit 1 en 3).",
        )

    def plain_secret_value(self, path: str, shown: str, param: Parameter) -> None:
        """N2 (ADR-0016 W2, ADR-0018): een geheimplek krijgt haar waarde van een gewone parameter (zonder
        geheimmarkering), of een gewone parameter met een geheimnaam wordt gebruikt (dan is ``shown`` leeg)."""
        who = (
            f"{shown} lijkt een geheim, maar krijgt zijn waarde van de gewone parameter {param.name!r}"
            if shown
            else f"De gewone parameter {param.name!r} heeft een geheimnaam"
        )
        self.err(
            "secret_in_form",
            path,
            f"{who}; zo komt het geheim in het formulier of de waarden. Een geheim maakt de app zelf, of de Hub "
            "(source generated + secret true).",
        )

    def param_name_rules(self, path: str, param: Parameter) -> bool:
        """ADR-0018: de naam van een gebruikte parameter telt ook. Een accountnaam geeft N3 (ook bij een geheim dat de
        Hub maakt), net als een geheimmarkering met een accountwoord in de eigen naam (ADR-0022,
        :func:`marked_account_param`); een geheimnaam zonder geheimmarkering geeft N2. Niet bij type ``boolean`` zonder
        geheimmarkering (ADR-0019, ADR-0021). Geeft ``True`` als er een fout is gemeld."""
        if not param_name_counts(param):
            return False
        if is_account_name(param.name) or marked_account_param(param):
            self.account_value(path, shown_key(param.name))
            return True
        if is_secret_name(param.name) and not is_marked_secret(param):
            self.plain_secret_value(path, "", param)
            return True
        return False

    def ref(
        self, ref: ParamRef, path: str, allowed_types: Iterable[str], what: str, *, name_rules: bool = True
    ) -> Parameter | None:
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
        if name_rules:
            self.param_name_rules(path, param)
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
        self.accounts()
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

    # -- accounts (aanvulling C-12, ADR-0016 W1, ADR-0023 besluit 1)
    def accounts(self) -> None:
        """W1: de combinaties uit de tabel van ADR-0016, de genoemde controle en haar lengte. Een melding noemt het pad en
        de regel, nooit de waarde van een veld (V-094)."""
        accounts = self.m.accounts
        checks = {c.id: idx for idx, c in enumerate(self.m.post_install_checks)}
        has_admin = any(a.rol == "beheerder" for a in accounts)
        ids: set[str] = set()
        admin_seen = False
        too_long: set[int] = set()
        for idx, a in enumerate(accounts):
            path = f"accounts[{idx}]"
            bad: list[tuple[str, str]] = []
            if a.id in ids:
                self.err("duplicate_name", f"{path}.id", "Deze account-id staat al eerder in accounts.")
            ids.add(a.id)
            if a.rol == "beheerder":
                if admin_seen:
                    bad.append(("rol", "een manifest heeft hooguit één beheerder"))
                admin_seen = True
                if a.aanmaak != "eerste_gebruik":
                    bad.append(("aanmaak", "een beheerder ontstaat alleen met aanmaak 'eerste_gebruik'"))
                bad += [
                    (f, f"een beheerder heeft geen {f} (hij heeft alle rechten)")
                    for f in ("afnemer", "rechten")
                    if getattr(a, f) is not None
                ]
            else:
                if a.aanmaak != "door_beheerder":
                    bad.append(("aanmaak", "een dienstaccount ontstaat alleen met aanmaak 'door_beheerder'"))
                if a.afnemer is None:
                    bad.append(
                        ("afnemer", "een dienstaccount noemt zijn afnemer (de app_id die de aanmelding gebruikt)")
                    )
                elif a.afnemer == self.m.app_id:
                    bad.append(("afnemer", "de afnemer is de app zelf; een dienstaccount is voor een andere app"))
                if a.rechten is None:
                    bad.append(("rechten", "een dienstaccount noemt zijn rechten in de app"))
                if not has_admin:
                    bad.append(("rol", "een dienstaccount vraagt een beheerder in hetzelfde manifest die het maakt"))
            for field, why in bad:
                self.err("account_ongeldig", f"{path}.{field}", f"Ongeldig account: {why} (ADR-0016 W1).")
            check = checks.get(a.controle)
            if check is None:
                self.err(
                    "account_controle_ontbreekt",
                    f"{path}.controle",
                    "Het account noemt een controle die niet in post_install_checks staat; die controle zegt de "
                    "beheerder wat hij doet en houdt de app tot de bevestiging 'niet gezond' (ADR-0016 W1).",
                )
            elif len(self.m.post_install_checks[check].beschrijving_nl) > ACCOUNT_CHECK_MAX and check not in too_long:
                too_long.add(check)
                self.err(
                    "account_controle_te_lang",
                    f"post_install_checks[{check}].beschrijving_nl",
                    f"Deze controle wordt door een account genoemd en haar beschrijving is langer dan {ACCOUNT_CHECK_MAX} "
                    "tekens; het beleid van de Hub weigert dat (ADR-0016 W1). Verwijs naar de rechten in het manifest "
                    "in plaats van ze op te sommen.",
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
            if is_secret_name(p.name) and not is_marked_secret(p):
                # N1 (ADR-0016 W2): een geheimnaam zonder geheimmarkering met een letterlijke waarde in het manifest
                field = next((f for f in ("default", "example") if has_value(getattr(p, f))), None)
                if field is not None:
                    self.err(
                        "secret_literal_forbidden",
                        f"{path}.{field}",
                        f"Parameter {p.name!r} lijkt een geheim en heeft een letterlijke {field}; de waarde van een "
                        "geheim staat nooit in het manifest.",
                    )
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
                if is_secret_name(env.name) and has_value(env.value):  # N1 (ADR-0016 W2)
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
                    name_rules=False,  # per plek één code: de naam van de plek en die van de parameter samen
                )
                names = param is not None and param_name_counts(param)  # ADR-0019: niet bij een gewone boolean
                if is_account_name(env.name):
                    self.account_value(path, shown_key(env.name))
                elif param is not None and names and is_account_name(param.name):
                    self.account_value(path, shown_key(param.name))
                elif param is not None and names and is_secret_name(param.name) and has_account_word(env.name):
                    # ADR-0019: een parameter met een geheimnaam op een plek met een accountwoord (ADMIN_PW)
                    self.account_value(path, shown_key(param.name))
                elif param is not None and is_marked_secret(param) and has_account_word(env.name):
                    # ADR-0021: ook een parameter met een geheimmarkering zonder geheimwoord in de naam (admin_pw)
                    self.account_value(path, shown_key(param.name))
                elif param is not None and marked_account_param(param):
                    # ADR-0022: een geheimmarkering met een accountwoord in de eigen naam, ook onder een gewone naam (X)
                    self.account_value(path, shown_key(param.name))
                elif param is not None and param.hub_generated_secret and not is_secret_name(env.name):
                    # ADR-0023 besluit 4 (variant B): alleen onder een geheimnaam kan de validator een aanmelding uitsluiten
                    self.err(
                        "secret_in_form",
                        path,
                        f"{env.name} krijgt een geheim dat de Hub maakt ({param.name!r}), maar de naam is geen geheimnaam; "
                        "dan kan de validator niet uitsluiten dat het de aanmelding van een beheerder of dienstaccount is "
                        "(fail-closed, ADR-0023). Kies een naam met een geheimwoord en zonder accountwoord.",
                    )
                elif param is not None and param.hub_generated_secret:
                    # contract 1.3 §3.1 a: de Hub maakt het geheim (vault) en geeft het alleen in de values mee
                    self.warn(
                        "secret_in_form",
                        path,
                        f"{env.name} krijgt een geheim dat de Hub maakt ({param.name!r}, source generated); het staat "
                        "nooit in het manifest, een log of evidence.",
                        acceptable=True,
                    )
                elif param is not None and is_marked_secret(param):
                    self.err(
                        "secret_in_form",
                        path,
                        f"{env.name} zou een geheim ({param.name!r}) in het Custom App-formulier zetten; dat is verboden.",
                    )
                elif param is not None and is_secret_name(env.name):
                    self.plain_secret_value(path, shown_key(env.name), param)
                elif param is not None and names and is_secret_name(param.name):
                    self.plain_secret_value(path, "", param)
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
        for path, name, key, where in _param_refs(m.values, "values"):
            param = self.params.get(name)
            self.used_params.add(name)
            names = param is not None and param_name_counts(param)  # ADR-0019: niet bij een gewone boolean
            # ADR-0018: een accountplek (ook via een voorouder) of een parameter met een accountnaam; ADR-0019: ook een
            # parameter met een geheimnaam op een plek waarvan de eigen naam of een voorouder een accountwoord heeft;
            # ADR-0021: op zo'n plek ook een parameter met een geheimmarkering zonder geheimwoord in de naam;
            # ADR-0022: op elke plek een parameter met een geheimmarkering en een accountwoord in de eigen naam
            account = where.account or (
                param is not None
                and (
                    (names and is_account_name(param.name))
                    or marked_account_param(param)
                    or (where.account_word and ((names and is_secret_name(param.name)) or is_marked_secret(param)))
                )
            )
            if account:
                self.account_value(path, where.shown_secret if where.account else shown_key(name))
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
            if account:
                continue  # N3 staat er al; geen waarschuwing secret_in_form op dit pad
            if is_marked_secret(param):
                self.warn(
                    "secret_in_form",
                    path,
                    f"Geheim {name!r} gaat als (door TrueNAS als privé gemarkeerde) catalogus-waarde mee; "
                    "controleer dat het niet in logs of evidence verschijnt."
                    + (" De Hub maakt het (vault, contract 1.3 §3.1 a)." if param.hub_generated_secret else ""),
                    acceptable=param.hub_generated_secret,
                )
            elif where.secret:
                self.plain_secret_value(path, where.shown_secret, param)
            elif names and is_secret_name(param.name):
                self.plain_secret_value(path, "", param)

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
            sub = f"{path}.{shown_key(str(key))}"
            if isinstance(key, str) and _HUB_RESERVED.match(key):
                yield sub
            yield from _hub_names_in_values(item, sub)
    elif isinstance(value, list):
        for idx, item in enumerate(value):
            yield from _hub_names_in_values(item, f"{path}[{idx}]")


def check_manifest(manifest: Manifest) -> list[Finding]:
    """Alle semantische regels op een (schema-geldig) manifest."""
    return _Rules(manifest).run()
