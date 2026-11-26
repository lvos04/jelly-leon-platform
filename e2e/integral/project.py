"""Bedien uitsluitend het eigen Compose-project. Foutuitvoer en argumenten kunnen koppelgegevens bevatten."""

from __future__ import annotations

import errno
import hashlib
import json
import os
import re
import socket
import subprocess
import tempfile
from pathlib import Path
from typing import Any

import yaml

MAP = Path(__file__).resolve().parent
ROOT = MAP.parents[1]
KERN = ("hub", "executor", "truenas", "lifecycle")
BRONLABEL = "nl.jelly-leon.broncommit"


class Proeffout(RuntimeError):
    """Alleen vaste, publieke diagnose en de gemeten exitcode mogen in het dossier."""

    def __init__(self, melding: str, code: int = 1):
        super().__init__(melding)
        self.code = code


class BouwNodig(Proeffout):
    """De selectie kan pas verder nadat de aanroeper de vereiste image heeft gebouwd."""


def opdracht(args: list[str], *, env: dict[str, str] | None = None, invoer: str | None = None,
             tijd: int = 60) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(args, env=env, input=invoer, capture_output=True, text=True,  # noqa: S603
                              errors="replace", timeout=tijd, check=False)
    except subprocess.TimeoutExpired:
        raise Proeffout("Tijdslimiet van de opdracht bereikt", 124) from None
    except OSError:
        raise Proeffout("Opdracht kon niet worden gestart", 127) from None


def gecontroleerd(resultaat: subprocess.CompletedProcess[str], melding: str) -> str:
    if resultaat.returncode:
        raise Proeffout(melding, resultaat.returncode)
    return resultaat.stdout


def broncommit(map_: Path) -> str:
    return gecontroleerd(opdracht(["git", "-C", str(map_), "rev-parse", "HEAD"]), "Broncommit ontbreekt").strip()


class Project:
    def __init__(self, naam: str):
        self.naam = naam
        self.env = os.environ.copy()
        self.env.setdefault("INTEGRAAL_BRONMAP", str(ROOT.parent))
        self.env.setdefault("INTEGRAAL_TAG", hashlib.sha256(str(ROOT).encode()).hexdigest()[:12])
        # Een nieuwe proef begint altijd met een ongekoppelde lifecycle-dienst.
        for k in ("INTEGRAAL_ENROLLMENT", "INTEGRAAL_VINGERAFDRUK"):
            self.env.pop(k, None)
        self.cmd = ["docker", "compose", "-f", str(MAP / "compose.yaml"), "-p", naam]
        self.diensten = json.loads(self.compose("--profile", "*", "config", "--format", "json"))["services"]

    def kies_poorten(self, diensten: tuple[str, ...]) -> None:
        for naam in diensten:
            variabele = "INTEGRAAL_" + naam.upper().replace("-", "_") + "_POORT"
            for poort in self.diensten[naam].get("ports", []):
                with socket.socket() as proef:
                    try:
                        proef.bind(("localhost", int(poort["published"])))
                    except OSError as fout:
                        if fout.errno != errno.EADDRINUSE:
                            raise
                        if variabele in self.env:
                            raise Proeffout(f"De opgegeven hostpoort voor {naam} is bezet") from None
                        self.env[variabele] = "0"  # Docker kiest de vrije poort bij het starten.

    def compose(self, *args: str, env: dict[str, str] | None = None, invoer: str | None = None,
                tijd: int = 120) -> str:
        res = opdracht([*self.cmd, *args], env={**self.env, **(env or {})}, invoer=invoer, tijd=tijd)
        return gecontroleerd(res, "Compose-opdracht mislukt")

    def images(self, diensten: tuple[str, ...]) -> dict[str, dict[str, str]]:
        images = {}
        for naam in diensten:
            dienst = self.diensten[naam]
            ref = dienst["image"]
            res = opdracht(["docker", "image", "inspect", ref, "--format", "{{json .}}"])
            if res.returncode:
                # Een daemonfout is geen ontbrekende optionele image.
                if not any(tekst in res.stderr.lower() for tekst in ("no such image", "no such object")):
                    raise Proeffout(f"Image-inspectie mislukt voor {naam}", res.returncode)
                if naam == "jelly-leon":
                    continue
                raise BouwNodig(f"Image ontbreekt voor {naam}. Voer eerst --bouw --profiel {dienst['profiles'][0]} uit")
            data = json.loads(res.stdout)
            labels = data["Config"].get("Labels") or {}
            if "build" in dienst and not re.fullmatch(r"[a-f0-9]{40}", labels.get(BRONLABEL, "")):
                profiel = dienst["profiles"][0]
                raise BouwNodig(f"Broncommit ontbreekt voor {naam}. Voer eerst --bouw --profiel {profiel} uit")
            images[naam] = {"image": ref, "image_id": data["Id"],
                            "broncommit": labels.get(BRONLABEL) or labels.get("org.opencontainers.image.revision", ""),
                            "digest": ", ".join(data.get("RepoDigests") or [])}
            if "build" in dienst:
                images[naam]["bronstatus"] = labels.get("nl.jelly-leon.bronstatus", "onbekend")
            if naam == "mediaguard":
                for deel in ("encode", "web", "sonarr", "radarr"):
                    images[naam][f"{deel}_image_id"] = labels.get(f"nl.jelly-leon.mg.{deel}", "")
        return images

    def controleer_images(self, diensten: tuple[str, ...]) -> None:
        verwacht = json.loads(self.env["INTEGRAAL_IMAGE_IDS"])
        for naam in diensten:
            cid = self.compose("ps", "-q", naam).strip()
            res = opdracht(["docker", "inspect", "--format", "{{.Image}}", cid])
            if gecontroleerd(res, f"Containerinspectie mislukt voor {naam}").strip() != verwacht[naam]:
                raise Proeffout(f"Image van {naam} is tussen selectie en meting gewijzigd")

    def start(self, diensten: tuple[str, ...], **env: str) -> None:
        self.kies_poorten(diensten)
        self.compose("up", "-d", "--no-build", "--pull", "never", "--no-deps", *diensten, env=env)
        self.controleer_images(diensten)

    def ruim(self) -> None:
        self.compose("--profile", "*", "down", "-v", "--timeout", "15", tijd=180)
        label = f"label=com.docker.compose.project={self.naam}"
        for query in (["ps", "-aq"], ["network", "ls", "-q"], ["volume", "ls", "-q"]):
            res = opdracht(["docker", *query, "--filter", label])
            if gecontroleerd(res, "Controle na opruimen mislukt").strip():
                raise Proeffout("Eigen Compose-resources achtergebleven na down -v")

    def bouw(self, profiel: str | None) -> None:
        if profiel is not None and profiel not in {p for d in self.diensten.values() for p in d["profiles"]}:
            raise Proeffout("Onbekend bouwprofiel", 2)
        gebouwd = set()
        for naam, dienst in self.diensten.items():
            if (profiel is not None and profiel not in dienst["profiles"]) or dienst["image"] in gebouwd:
                continue
            if naam == "jelly-leon":
                print("jelly-leon:p6 komt uit S-P6-30", flush=True)
                continue
            print(f"Bouwcontrole {naam}", flush=True)
            if "build" not in dienst:
                self.publieke_image(naam, dienst)
                continue
            context = Path(dienst["build"]["context"])
            if not (context / dienst["build"].get("dockerfile", "Dockerfile")).is_file():
                raise Proeffout(f"Bouwbron voor {naam} nog niet beschikbaar")
            gitstatus = opdracht(["git", "--no-optional-locks", "-C", str(context), "status", "--porcelain"])
            vuil = bool(gecontroleerd(gitstatus, f"Bronstatus ontbreekt voor {naam}").strip())
            env = {"INTEGRAAL_BRONCOMMIT": broncommit(context),
                   "INTEGRAAL_BRONSTATUS": "gewijzigd" if vuil else "schoon"}
            if naam == "mediaguard":
                env.update(self.mediaguard(context.parents[1]))
            try:
                self.compose("build", naam, env=env, tijd=7200)
            except Proeffout as fout:
                raise Proeffout(f"Bouw van {naam} mislukt (exit {fout.code})", fout.code) from None
            gebouwd.add(dienst["image"])
            print(f"{naam} {self.images((naam,))[naam]['image_id']}", flush=True)

    def publieke_image(self, naam: str, dienst: dict[str, Any]) -> None:
        # Alleen de drie publieke, op digest gepinde meldimages zijn extern. Anonieme aanvraag.
        manifest = yaml.safe_load((ROOT / f"install/manifests/{naam}.manifest.yaml").read_text())["image"]
        ref = f"{manifest['repository']}:{manifest['tag']}@{manifest['digest']}"
        if dienst["image"] != ref:
            raise Proeffout(f"Digest van {naam} wijkt af van het installatiemanifest")
        with tempfile.TemporaryDirectory(dir=os.environ.get("TMPDIR", MAP)) as config:
            res = opdracht(["docker", "--config", config, "pull", ref], tijd=600)
        gecontroleerd(res, f"Anoniem ophalen van {naam} mislukt")

    def mediaguard(self, repo: Path) -> dict[str, str]:
        invoer = {}
        tag = self.env["INTEGRAAL_TAG"]
        for deel in ("encode", "web", "sonarr", "radarr"):
            ref = f"jlp-integraal/mg-{deel}:{tag}"
            if deel in {"encode", "web"}:
                args = ["bash", str(repo / "scripts/build-all.sh"), "--tag", tag, "--only", deel]
            else:
                args = ["bash", str(repo / f"forks/{deel}/build.sh"), "-t", ref]
            env = {**self.env, "IMAGE_PREFIX": "jlp-integraal/mg-", "IMAGE_REPO": f"jlp-integraal/mg-{deel}-{tag}"}
            gecontroleerd(opdracht(args, env=env, tijd=7200), f"Bouw van MediaGuard-onderdeel {deel} mislukt")
            inspectie = opdracht(["docker", "image", "inspect", "--format", "{{.Id}}", ref])
            image_id = gecontroleerd(inspectie, f"Image-ID ontbreekt voor {deel}").strip()
            invoer[f"INTEGRAAL_MG_{deel.upper()}_ID"] = image_id
        return invoer
