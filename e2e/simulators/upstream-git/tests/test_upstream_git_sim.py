"""Toestandstabel upstream-git-simulator: tags in de bron, compatibele en onverenigbare tag tegen de fork (EX48)."""

import json
import subprocess
import sys
from pathlib import Path

import pytest
from upstream_git_sim import BASIS, COMPATIBEL, GEPATCHT, ONVERENIGBAAR, bouw


def git(repo: str | Path, *args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    cmd = ["git", "-C", str(repo), "-c", "user.name=T", "-c", "user.email=t@example.invalid",
           "-c", "commit.gpgsign=false", *args]
    return subprocess.run(cmd, capture_output=True, text=True, check=check)


@pytest.fixture
def sim(tmp_path: Path) -> dict[str, str]:
    return bouw(tmp_path / "upstream")


def test_bron_heeft_precies_de_drie_tags(sim: dict[str, str]) -> None:
    uit = git(sim["bron"], "ls-remote", "--tags", sim["bron"]).stdout
    peeled = dict(reversed(r.split("\t")) for r in uit.splitlines() if r.endswith("^{}"))
    assert peeled == {f"refs/tags/{t}^{{}}": sim[t] for t in (BASIS, COMPATIBEL, ONVERENIGBAAR)}


def test_fork_staat_op_de_basis(sim: dict[str, str]) -> None:
    fork = sim["fork"]
    basis = json.loads((Path(fork) / "tools/jelly-leon/baseline.json").read_text())
    assert basis["upstream_commit"] == sim[BASIS] and basis["upstream_tag"] == BASIS
    assert git(fork, "merge-base", "HEAD", sim[BASIS]).stdout.strip() == sim[BASIS]
    assert git(fork, "remote", "get-url", "upstream").stdout.strip() == sim["bron"]
    assert git(fork, "status", "--porcelain").stdout == ""


def test_compatibele_tag_merget_schoon(sim: dict[str, str]) -> None:
    fork = sim["fork"]
    git(fork, "fetch", "-q", "upstream", f"refs/tags/{COMPATIBEL}:refs/sync/{COMPATIBEL}")
    assert git(fork, "merge", "--no-edit", "--no-ff", f"refs/sync/{COMPATIBEL}", check=False).returncode == 0
    assert git(fork, "diff", "--name-only", "--diff-filter=U").stdout == ""


def test_onverenigbare_tag_geeft_conflict_op_de_core_patch(sim: dict[str, str]) -> None:
    fork = sim["fork"]
    git(fork, "fetch", "-q", "upstream", f"refs/tags/{ONVERENIGBAAR}:refs/sync/{ONVERENIGBAAR}")
    merge = git(fork, "merge", "--no-edit", "--no-ff", f"refs/sync/{ONVERENIGBAAR}", check=False)
    assert merge.returncode != 0
    assert git(fork, "diff", "--name-only", "--diff-filter=U").stdout.split() == [GEPATCHT]
    git(fork, "merge", "--abort")


def test_alleen_de_onverenigbare_tag_raakt_het_gepatchte_bestand(sim: dict[str, str]) -> None:
    def gewijzigd(a: str, b: str) -> list[str]:
        return git(sim["bron"], "diff", "--name-only", a, b).stdout.split()

    assert GEPATCHT not in gewijzigd(sim[BASIS], sim[COMPATIBEL])
    assert GEPATCHT in gewijzigd(sim[COMPATIBEL], sim[ONVERENIGBAAR])


def test_geen_kopie_van_upstream_broncode(sim: dict[str, str]) -> None:
    bestanden = git(sim["bron"], "ls-tree", "-r", "-l", ONVERENIGBAAR).stdout.splitlines()
    assert len(bestanden) <= 5 and all(int(r.split()[3]) < 200 for r in bestanden)


def test_niet_lege_doelmap_wordt_geweigerd(tmp_path: Path) -> None:
    (tmp_path / "iets").write_text("x")
    with pytest.raises(FileExistsError):
        bouw(tmp_path)
    assert [p.name for p in tmp_path.iterdir()] == ["iets"]


def test_startmodule_met_relatieve_doelmap_geeft_absolute_paden(tmp_path: Path) -> None:
    script = Path(__file__).parents[1] / "upstream_git_sim.py"
    uit = json.loads(subprocess.run([sys.executable, str(script), "--doel", "u"], cwd=tmp_path,
                                    capture_output=True, text=True, check=True).stdout)
    assert set(uit) == {"bron", "fork", BASIS, COMPATIBEL, ONVERENIGBAAR}
    assert uit["bron"] == str(tmp_path.resolve() / "u/upstream.git")
    assert git(uit["fork"], "ls-remote", "--tags", "upstream").returncode == 0
