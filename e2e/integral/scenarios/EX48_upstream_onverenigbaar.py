"""Voer de merge- en bundelstappen van de fork uit op een lokaal Git-volume met twee simulatortags."""

import json
from pathlib import Path
from typing import Any

import yaml

from e2e.integral.bewijs import schrijf
from e2e.integral.project import broncommit, opdracht
from e2e.integral.scenarios.conftest import Kern
from e2e.integral.scenarios.hubveiligheid import schrijfacties

SCENARIO = {
    "groepen": ("opslag",), "eigenaar": "jelly-leon-platform", "diensten": ("upstream-git",),
    "verwachting": "Bij een conflict volgt geen reviewbundel of uitrol. Een compatibele tag levert een bundel.",
}


def test_sync_en_overdracht(kern: Kern) -> None:
    p = kern.net.project
    repo = Path(p.env["INTEGRAAL_BRONMAP"]) / "jelly-leon"
    jobs = yaml.safe_load((repo / ".forgejo/workflows/upstream-sync.yml").read_text())["jobs"]

    def stap(stappen: list[dict[str, Any]], zoek: str) -> str:
        [tekst] = [s["run"] for s in stappen if zoek in s.get("run", "")]
        return str(tekst)

    merge = stap(jobs["sync"]["steps"], "upstream_sync.py merge")
    bundel = stap(jobs["sync"]["steps"], "git bundle create")
    overdracht = stap(jobs["push"]["steps"], "Geen geslaagde sync")
    git = ("exec", "-T", "-w", "/proef/sim/fork", "upstream-git", "git")
    p.compose("exec", "-T", "upstream-git", "python", "/simulator.py", "--doel", "/proef/sim")
    p.compose("exec", "-T", "-w", "/proef/sim/fork", "upstream-git", "bash", "-ec", """
        mkdir /proef/runner
        cp /synchulp/{upstream_sync,jl_common,patch_register_check}.py tools/jelly-leon/
        echo '/tools/jelly-leon/*.py' >> .git/info/exclude
        git update-ref refs/remotes/origin/master HEAD
    """)

    def draai(script: str, tag: str, verwacht: int = 0) -> None:
        cmd = [*p.cmd, "exec", "-T", "-w", "/proef/sim/fork", "-e", "SYNC_TAG=" + tag, "-e", "SYNC_METHODE=auto",
               "-e", "UPSTREAM_URL=/proef/sim/upstream.git", "-e", "RUNNER_TEMP=/proef/runner",
               "-e", "GITHUB_STEP_SUMMARY=/proef/runner/samenvatting", "-e", "DEFAULT_BRANCH=master",
               "-e", "PUSH_TOKEN=", "-e", "OPHALEN=success", "upstream-git", "bash", "-seu"]
        res = opdracht(cmd, env=p.env, invoer=script)
        assert res.returncode == verwacht

    oude_refs = p.compose(*git, "show-ref", "--heads", "--tags")
    oude_kop = p.compose(*git, "rev-parse", "HEAD")
    oude_tak = p.compose(*git, "branch", "--show-current")
    draai(merge, "v1.1", 2)
    res = json.loads(p.compose("exec", "-T", "upstream-git", "cat", "/proef/runner/sync-resultaat.json"))
    assert res["status"] == "incompatibel_teruggedraaid"
    assert p.compose(*git, "rev-parse", "HEAD") == oude_kop and p.compose(*git, "branch", "--show-current") == oude_tak
    assert p.compose(*git, "show-ref", "--heads", "--tags") == oude_refs
    assert not p.compose(*git, "status", "--porcelain")
    draai(bundel, "v1.1")
    draai("test ! -e /proef/runner/sync.bundle", "v1.1")
    draai(overdracht, "v1.1")
    draai("grep -Fq 'Geen geslaagde sync' /proef/runner/samenvatting", "v1.1")
    assert schrijfacties(kern) == []
    draai(merge, "v1.0.1")
    draai(bundel, "v1.0.1")
    refs = p.compose(*git, "bundle", "list-heads", "/proef/runner/sync.bundle")
    assert {r.split()[1] for r in refs.splitlines()} == {"refs/heads/sync/v1.0.1", "refs/tags/upstream-v1.0.1"}
    assert schrijfacties(kern) == []
    schrijf(kern.net.dossier / "metingen.json", {"fork_broncommit": broncommit(repo), "conflict_exit": 2,
            "refs_behouden": True, "bundels_na_conflict": 0, "schrijfacties": 0, "refs_in_compatibele_bundel": 2})
