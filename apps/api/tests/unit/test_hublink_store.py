"""Contract 1.2 §3.5: ``/appdata/secrets/hub-link.json`` — atomair, 0600, vergrendeld, opnieuw lezen bij wijziging,
witte lijst naar buiten, fail-closed bij een beschadigd bestand."""

from __future__ import annotations

import json
import multiprocessing
import os
import stat
from pathlib import Path
from typing import Any

import pytest

from lifecycle.hublink import (
    MAX_USED_TOKENS,
    HubLinkCorrupt,
    HubLinkStore,
    add_used_token,
    clear_link,
    empty_doc,
    validate_doc,
)

TOKEN = "svc_" + "T" * 43


def linked_doc(doc: dict[str, Any]) -> None:
    entry = {"kid": "k1", "jwk": {"kty": "OKP", "crv": "Ed25519", "x": "11qYAYKxCrfVS_7TyWQHOg7hcvPapiMlrwIaaPcHURo"},
             "thumbprint": "kPrK_qmxVWaYVA9wwBF6Iuo3vVzz7TxHCTwXBygrS4k", "pinned_at": "2026-11-09T00:00:00Z",
             "source": "enroll"}
    policy = {**entry, "kid": "p1", "jwk": {**entry["jwk"], "x": "A6EHv_POEL4dcN0Y50vAmWfk1jCbpQ1fHdyGZBJVMbg"},
              "thumbprint": "1IG2tMH7J2wbJZnOf8LJzQitKf7LMvoAElsuDMVM54Y"}
    doc.update(state="gekoppeld", hub_url="http://hub.example", service_id="7c0a3c3e-6f0b-4a8e-9c4e-0f1d2e3a4b5c",
               first_linked_at="2026-11-09T00:00:00Z", linked_at="2026-11-09T00:00:00Z", service_token=TOKEN,
               service_token_last4=TOKEN[-4:],
               pins={"jwt": [entry], "policy": [policy], "keyset_fingerprint": "MEGG-8GFG-2E4Y-K2RQ",
                     "rotation_sequence": 0},
               pending={"kind": "token", "secret_sha256": "a" * 64, "hub_url": "http://hub.example",
                        "started_at": "2026-11-09T00:00:00Z", "attempts": 1})


def test_missing_file_is_never_linked(tmp_path: Path) -> None:
    link = HubLinkStore(tmp_path).read()
    assert link.state == "nooit_gekoppeld" and not link.linked and not link.ever_linked and not link.corrupt


def test_atomic_write_with_0600_and_lock_file(tmp_path: Path) -> None:
    store = HubLinkStore(tmp_path)
    link = store.update(linked_doc)
    assert link.linked and link.service_token == TOKEN and link.keyset_fingerprint == "MEGG-8GFG-2E4Y-K2RQ"
    assert stat.S_IMODE(store.path.stat().st_mode) == 0o600
    assert store.lock_path.exists() and stat.S_IMODE(store.lock_path.stat().st_mode) == 0o600
    assert not [p for p in tmp_path.iterdir() if p.name.endswith(".tmp")]  # geen tijdelijke resten
    assert json.loads(store.path.read_text())["schema_version"] == "1.2"


def test_other_process_sees_change_immediately(tmp_path: Path) -> None:
    api, worker = HubLinkStore(tmp_path), HubLinkStore(tmp_path)  # twee "processen" met eigen cache
    worker.update(linked_doc)
    assert api.read().linked
    worker.update(lambda d: clear_link(d, "ontkoppeld"))
    assert api.read().state == "ontkoppeld" and api.read().service_token is None


def _bump(path: str, n: int) -> None:
    store = HubLinkStore(Path(path))
    for i in range(n):
        store.update(lambda d, i=i: add_used_token(d, f"{(os.getpid() * 1000 + i) % 16**8:064x}"))


def test_concurrent_writers_lose_no_update(tmp_path: Path) -> None:
    HubLinkStore(tmp_path).update(lambda d: None)
    ctx = multiprocessing.get_context("fork")
    procs = [ctx.Process(target=_bump, args=(str(tmp_path), 5)) for _ in range(3)]
    for p in procs:
        p.start()
    for p in procs:
        p.join(30)
        assert p.exitcode == 0
    assert len(HubLinkStore(tmp_path).read().used_tokens) == 15  # vergrendeling: geen verloren schrijfactie


def test_public_view_is_a_whitelist(tmp_path: Path) -> None:
    store = HubLinkStore(tmp_path)
    store.update(linked_doc)
    view = store.read().connector_view()
    text = json.dumps(view)
    assert TOKEN not in text and "a" * 64 not in text and "secret_sha256" not in text
    assert view["koppelen_bezig"] is True and view["service_token_last4"] == TOKEN[-4:]
    assert view["pinned_kids"] == {"jwt": ["k1"], "policy": ["p1"]} and view["rotation_sequence"] == 0
    assert set(view) == {"state", "state_label_nl", "koppelen_bezig", "hub_url", "service_id", "via", "linked_at",
                         "first_linked_at", "keyset_fingerprint", "service_token_last4", "last_error", "beschadigd",
                         "pinned_kids", "rotation_sequence"}


def test_unlink_moves_pins_to_previous_and_keeps_first_linked_at(tmp_path: Path) -> None:
    store = HubLinkStore(tmp_path)
    store.update(linked_doc)
    link = store.update(lambda d: clear_link(d, "ontkoppeld"))
    assert link.state == "ontkoppeld" and link.doc["previous_pins"]["jwt"][0]["kid"] == "k1"
    assert link.pins is None and link.pin_map("jwt") == {} and link.ever_linked


def test_used_tokens_capped_at_20(tmp_path: Path) -> None:
    doc = empty_doc()
    for i in range(25):
        add_used_token(doc, f"{i:064x}")
    add_used_token(doc, f"{24:064x}")  # dubbel: niet nog eens
    assert len(doc["used_enrollment_token_sha256"]) == MAX_USED_TOKENS
    assert doc["used_enrollment_token_sha256"][-1] == f"{24:064x}"


@pytest.mark.parametrize(
    "content",
    ["{ kapot", "[]", json.dumps({"state": "onbekend"}), json.dumps({"state": "gekoppeld"}),
     json.dumps({**empty_doc(), "service_token": TOKEN}), json.dumps({**empty_doc(), "pending": {"kind": "x"}})],
)
def test_corrupt_file_fails_closed_and_is_only_replaced_on_request(tmp_path: Path, content: str) -> None:
    store = HubLinkStore(tmp_path)
    store.path.write_text(content)
    link = store.read()
    assert link.corrupt and not link.linked and link.ever_linked  # nooit het koppelformulier tonen
    assert link.public()["beschadigd"] is True and link.public()["state"] == "ontkoppeld"
    with pytest.raises(HubLinkCorrupt):
        store.update(lambda d: None)
    fresh = store.update(linked_doc, replace_corrupt=True)
    assert fresh.linked
    assert [p for p in tmp_path.iterdir() if p.name.startswith("hub-link.json.beschadigd-")]


def test_validate_never_writes_an_invalid_doc(tmp_path: Path) -> None:
    store = HubLinkStore(tmp_path)
    with pytest.raises(HubLinkCorrupt):
        store.update(lambda d: d.update(state="gekoppeld"))  # zonder token/pins
    assert not store.path.exists()
    with pytest.raises(HubLinkCorrupt):
        validate_doc({**empty_doc(), "used_enrollment_token_sha256": ["geen-hex"]})
