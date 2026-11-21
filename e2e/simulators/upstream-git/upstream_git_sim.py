"""Upstream-git-simulator (alleen tests): een lokale git-bron die de Jellyfin-upstream nabootst, met een fork ernaast.

Protocol en bronnen: ``e2e/simulators/upstream-git/README.md``. Geen kopie van Jellyfin-broncode: alleen het minimum
dat ``jelly-leon:tools/jelly-leon/upstream_sync.py`` leest. Tags: ``v1.0.0`` (basis van de fork), ``v1.0.1``
(compatibel) en ``v1.1`` (onverenigbaar: wijzigt het bestand waar de fork een geregistreerde core-patch op heeft).
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
from pathlib import Path

BASIS, COMPATIBEL, ONVERENIGBAAR = "v1.0.0", "v1.0.1", "v1.1"
GEPATCHT = ".github/workflows/bot.yml"

PATCHES = f"""# Patchregister (simulator)

## JL-0001 — Repository-guard op upstream-workflow

| Veld | Waarde |
|---|---|
| Status | actief |
| Doel | Upstream-bot-workflow niet laten draaien in de fork |
| Bestanden | `{GEPATCHT}` |
| Waarom geen plugin/configuratie | Workflowbestanden zijn niet via een plugin aan te passen |
| Upstream-PR-kans | laag |
| Test | upstream-git-simulator |
"""
FORK_PATHS = "README.jelly-leon.md\ndocs/platform-v2/\nplugins/\ntools/jelly-leon/\n"
PROPS = "<Project>\n  <PropertyGroup>\n    <JellyfinVersion>{v}</JellyfinVersion>\n  </PropertyGroup>\n</Project>\n"


def _git(repo: Path, *args: str) -> str:
    # Vaste identiteit en geen ondertekening: onafhankelijk van de git-instellingen van de machine.
    cmd = ["git", "-C", str(repo), "-c", "user.name=Simulator", "-c", "user.email=sim@example.invalid",
           "-c", "commit.gpgsign=false", "-c", "tag.gpgsign=false", *args]
    return subprocess.run(cmd, capture_output=True, text=True, check=True).stdout.strip()  # noqa: S603


def _schrijf(repo: Path, bestanden: dict[str, str]) -> None:
    for rel, tekst in bestanden.items():
        pad = repo / rel
        pad.parent.mkdir(parents=True, exist_ok=True)
        pad.write_text(tekst, encoding="utf-8")


def _release(repo: Path, tag: str, bestanden: dict[str, str]) -> str:
    _schrijf(repo, bestanden)
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", f"upstream {tag} (sim)")
    _git(repo, "tag", "-a", tag, "-m", tag)
    return _git(repo, "rev-parse", "HEAD")


def _versie(tag: str) -> str:
    return f'[assembly: AssemblyVersion("{tag[1:]}")]\n'


def bouw(doel: Path) -> dict[str, str]:
    """Maakt ``doel/upstream.git`` (bare, de bron voor ``--bron``) en ``doel/fork`` (werkkopie op ``v1.0.0``).

    Geeft de paden en de commit per tag terug. ``doel`` moet leeg zijn of nog niet bestaan.
    """
    doel = doel.resolve()  # absolute paden: git -C en --bron werken vanuit elke map
    if doel.exists() and any(doel.iterdir()):
        raise FileExistsError(f"{doel} is niet leeg")
    werk = doel / "upstream-werk"
    werk.mkdir(parents=True)
    _git(werk, "init", "-q", "-b", "master")
    commits = {BASIS: _release(werk, BASIS, {
        "LICENSE": "GNU GENERAL PUBLIC LICENSE (sim; geen echte upstream-tekst)\n",
        "SharedVersion.cs": _versie(BASIS), "src/Server.cs": "class Server {}\n",
        GEPATCHT: "name: bot\njobs:\n  a:\n    runs-on: ubuntu-latest\n"})}

    fork = doel / "fork"
    subprocess.run(["git", "clone", "-q", str(werk), str(fork)], check=True, capture_output=True)  # noqa: S603,S607
    _git(fork, "remote", "rename", "origin", "upstream")
    _git(fork, "tag", f"upstream-{BASIS}", commits[BASIS])
    basis = {"schema_version": "1", "upstream_tag": BASIS, "upstream_version": BASIS[1:],
             "upstream_commit": commits[BASIS], "local_upstream_tag": f"upstream-{BASIS}",
             "fork_version": f"{BASIS[1:]}-jl.0"}
    _schrijf(fork, {"tools/jelly-leon/baseline.json": json.dumps(basis, indent=2) + "\n",
                    "tools/jelly-leon/fork_paths.txt": FORK_PATHS,
                    "plugins/Directory.Build.props": PROPS.format(v=BASIS[1:]),
                    "docs/platform-v2/PATCHES.md": PATCHES, "README.jelly-leon.md": "# fork (sim)\n",
                    GEPATCHT: "name: bot\njobs:\n  a:\n    if: github.repository == 'up/stream'\n"
                              "    runs-on: ubuntu-latest\n"})
    _git(fork, "add", "-A")
    _git(fork, "commit", "-q", "-m", "fork-opzet (sim)")

    commits[COMPATIBEL] = _release(werk, COMPATIBEL, {"SharedVersion.cs": _versie(COMPATIBEL),
                                                      "src/Server.cs": "class Server { int Fix; }\n"})
    # Onverenigbaar: dezelfde regels als de core-patch van de fork wijzigen anders.
    commits[ONVERENIGBAAR] = _release(werk, ONVERENIGBAAR, {
        "SharedVersion.cs": _versie(ONVERENIGBAAR),
        GEPATCHT: "name: bot\njobs:\n  a:\n    runs-on: ubuntu-24.04\n    timeout-minutes: 5\n"})

    bron = doel / "upstream.git"
    subprocess.run(["git", "clone", "-q", "--bare", str(werk), str(bron)], check=True,  # noqa: S603,S607
                   capture_output=True)
    shutil.rmtree(werk)
    _git(fork, "remote", "set-url", "upstream", str(bron))
    return {"bron": str(bron), "fork": str(fork), **commits}


def main() -> None:
    parser = argparse.ArgumentParser(description="Upstream-git-simulator — alleen voor tests")
    parser.add_argument("--doel", type=Path, required=True, help="lege of nieuwe map")
    print(json.dumps(bouw(parser.parse_args().doel), indent=2))


if __name__ == "__main__":
    main()
