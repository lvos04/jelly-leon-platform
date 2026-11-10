"""Contract 1.3 §3.5 (C1.3-JLP-03): ``/appdata/secrets/hub-link.json`` — atomair, 0600, vergrendeld, opnieuw lezen
bij wijziging, witte lijst naar buiten, ``schema_version`` "1.3" schrijven en elke 1.x lezen, fail-closed bij een
onleesbaar bestand (ook verkeerde rechten of een andere major), vervangen alleen op verzoek met het origineel als
``hub-link.json.onleesbaar-<UTC-tijd>`` (0600) en ``unreadable_replaced_at``."""

from __future__ import annotations

import json
import multiprocessing
import os
import re
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
    assert json.loads(store.path.read_text())["schema_version"] == "1.3"


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
    # §3.5: precies de witte lijst (een maximum) + pinned_kids/rotation_sequence van §6
    assert set(view) == {"state", "koppelen_bezig", "hub_url", "service_id", "via", "linked_at", "first_linked_at",
                         "keyset_fingerprint", "service_token_last4", "last_error", "pinned_kids", "rotation_sequence"}


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
    ["{ kapot", "[]", json.dumps({"state": "onbekend", "schema_version": "1.3"}),
     json.dumps({"state": "gekoppeld", "schema_version": "1.3"}),
     json.dumps({**empty_doc(), "service_token": TOKEN}), json.dumps({**empty_doc(), "pending": {"kind": "x"}}),
     json.dumps({**empty_doc(), "schema_version": "2.0"}),  # andere major
     json.dumps({k: v for k, v in empty_doc().items() if k != "schema_version"}),  # verplicht veld ontbreekt
     json.dumps({**empty_doc(), "unreadable_replaced_at": 5})],
)
def test_corrupt_file_fails_closed_and_is_only_replaced_on_request(tmp_path: Path, content: str) -> None:
    store = HubLinkStore(tmp_path)
    store.path.write_text(content)
    os.chmod(store.path, 0o600)
    link = store.read()
    assert link.corrupt and link.onleesbaar and not link.linked and link.ever_linked  # nooit het koppelformulier
    view = link.public()
    assert view["state"] is None and view["last_error"]["code"] == "koppelstatus_onleesbaar"
    with pytest.raises(HubLinkCorrupt):
        store.update(lambda d: None)  # een gewone schrijfactie zet het nooit stil terug
    before = store.path.read_bytes()
    fresh = store.update(lambda d: d.update(pending={"kind": "token", "secret_sha256": "b" * 64, "hub_url": "http://h",
                                                     "started_at": "2026-11-10T00:00:00Z", "attempts": 1}),
                         replace_corrupt=True)
    assert not fresh.corrupt and fresh.doc["unreadable_replaced_at"] and fresh.doc["schema_version"] == "1.3"
    assert fresh.onleesbaar and fresh.ever_linked  # §3.5: zolang first_linked_at leeg is, blijft het "onleesbaar"
    assert fresh.public()["last_error"]["code"] == "koppelstatus_onleesbaar" and fresh.public()["koppelen_bezig"]
    kopie = [p for p in tmp_path.iterdir() if p.name.startswith("hub-link.json.onleesbaar-")]
    assert len(kopie) == 1 and kopie[0].read_bytes() == before and stat.S_IMODE(kopie[0].stat().st_mode) == 0o600
    assert re.fullmatch(r"hub-link\.json\.onleesbaar-\d{8}T\d{6}Z", kopie[0].name)
    linked = store.update(linked_doc)  # na een geslaagde koppeling (first_linked_at) is het gewoon gekoppeld
    assert linked.linked and not linked.onleesbaar and linked.doc["unreadable_replaced_at"]


def test_wrong_owner_or_mode_is_unreadable(tmp_path: Path) -> None:
    """§3.5: verkeerde rechten = onleesbaar (fail-closed), ook als de inhoud geldig is."""
    store = HubLinkStore(tmp_path)
    store.update(linked_doc)
    assert store.read().linked
    os.chmod(store.path, 0o644)
    assert store.read().corrupt and not store.read().linked
    os.chmod(store.path, 0o600)
    assert store.read().linked


def test_reader_accepts_any_1x_and_keeps_unknown_fields(tmp_path: Path) -> None:
    store = HubLinkStore(tmp_path)
    store.update(linked_doc)
    doc = json.loads(store.path.read_text())
    doc.update(schema_version="1.7", toekomstig_veld={"x": 1}, token_rotation_requested_at="2026-11-10T00:00:00Z",
               key_rotation_blocked_sequence=3)
    store.path.write_text(json.dumps(doc))
    os.chmod(store.path, 0o600)
    link = store.read()
    assert link.linked and link.doc["toekomstig_veld"] == {"x": 1}
    assert "toekomstig_veld" not in link.public() and "token_rotation_requested_at" not in link.connector_view()
    written = store.update(lambda d: None)
    assert written.doc["schema_version"] == "1.3" and written.doc["toekomstig_veld"] == {"x": 1}


def test_two_replacements_in_the_same_second_keep_both_originals(tmp_path: Path) -> None:
    store = HubLinkStore(tmp_path)
    for inhoud in ("{ eerste", "{ tweede"):
        store.path.write_text(inhoud)
        os.chmod(store.path, 0o600)
        store.update(lambda d: None, replace_corrupt=True)
    kopieën = sorted(p.read_text() for p in tmp_path.iterdir() if p.name.startswith("hub-link.json.onleesbaar-"))
    assert kopieën == ["{ eerste", "{ tweede"]


def test_validate_never_writes_an_invalid_doc(tmp_path: Path) -> None:
    store = HubLinkStore(tmp_path)
    with pytest.raises(HubLinkCorrupt):
        store.update(lambda d: d.update(state="gekoppeld"))  # zonder token/pins
    assert not store.path.exists()
    with pytest.raises(HubLinkCorrupt):
        validate_doc({**empty_doc(), "used_enrollment_token_sha256": ["geen-hex"]})
