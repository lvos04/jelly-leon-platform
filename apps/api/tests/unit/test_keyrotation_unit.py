"""Contract 1.3 §4.5: ondertekende sleutelrotatie — alleen met een op dat moment gepinde sleutel van hetzelfde doel,
``sequence`` zonder gaten, nooit de laatste sleutel weg, een ongeldig statement stopt alles."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from hub_sim import HubState

from lifecycle.hublink import HubLinkStore, now_iso
from lifecycle.keyrotation import apply_items
from lifecycle.keys import hub_jwk_to_pin, keyset_fingerprint, keyset_fingerprint_of_thumbprints


def linked_doc(store: HubLinkStore, sim: HubState) -> dict[str, Any]:
    pins = [hub_jwk_to_pin(k) for k in sim.jwks()["keys"]]
    policy = hub_jwk_to_pin(sim.policy_jwk())
    at = now_iso()
    link = store.update(lambda d: d.update(
        state="gekoppeld", hub_url="http://hub.example", service_id="s", first_linked_at=at, linked_at=at,
        service_token="svc_" + "A" * 43, service_token_last4="AAAA",
        pins={"jwt": [p.as_entry(at, "enroll") for p in pins], "policy": [policy.as_entry(at, "enroll")],
              "keyset_fingerprint": keyset_fingerprint_of_thumbprints([p.thumbprint for p in pins],
                                                                      [policy.thumbprint]),
              "rotation_sequence": 0}))
    return dict(link.doc)


def test_empty_list_changes_nothing(tmp_path: Path) -> None:
    doc = linked_doc(HubLinkStore(tmp_path), HubState())
    before = dict(doc["pins"])
    assert apply_items(doc, []) == (0, None)
    assert doc["pins"] == before and doc["last_error"] is None


def test_valid_jwt_then_policy_rotation(tmp_path: Path) -> None:
    sim = HubState()
    doc = linked_doc(HubLinkStore(tmp_path), sim)
    old_kid = sim.kid
    new_kid = sim.rotate_key("jwt", retire_old=False)
    new_policy = sim.rotate_key("policy", retire_old=True)
    applied, error = apply_items(doc, list(sim.rotations))
    assert (applied, error) == (2, None)
    kids = [e["kid"] for e in doc["pins"]["jwt"]]
    assert kids == [old_kid, new_kid] and [e["kid"] for e in doc["pins"]["policy"]] == [new_policy]
    assert doc["pins"]["rotation_sequence"] == 2
    assert doc["pins"]["keyset_fingerprint"] == keyset_fingerprint(sim.jwks()["keys"], [sim.policy_jwk()])
    assert doc["pins"]["keyset_fingerprint"] == sim.keyset_fingerprint()
    assert all(e["source"].startswith("rotatie:") for e in doc["pins"]["jwt"][1:])
    # nogmaals dezelfde lijst: al toegepast, niets dubbel
    assert apply_items(doc, list(sim.rotations)) == (0, None)


def test_signed_by_key_of_other_purpose_is_refused(tmp_path: Path) -> None:
    sim = HubState()
    doc = linked_doc(HubLinkStore(tmp_path), sim)
    sim.rotate_key("jwt", signer=sim.policy_keys[-1])  # jwt-wijziging door de policy-sleutel
    applied, error = apply_items(doc, list(sim.rotations))
    assert applied == 0 and error and "niet gepind" in error
    assert doc["last_error"]["code"] == "sleutelrotatie_ongeldig" and doc["pins"]["rotation_sequence"] == 0


def test_gap_in_sequence_stops_and_keeps_earlier_valid_ones(tmp_path: Path) -> None:
    sim = HubState()
    doc = linked_doc(HubLinkStore(tmp_path), sim)
    first = sim.rotate_key("jwt")
    sim.rotate_key("jwt", sequence=3)  # gat: 2 ontbreekt
    applied, error = apply_items(doc, list(sim.rotations))
    assert applied == 1 and error and "sequence 2" in error
    assert doc["pins"]["rotation_sequence"] == 1 and first in [e["kid"] for e in doc["pins"]["jwt"]]


def test_retiring_the_last_key_is_refused(tmp_path: Path) -> None:
    sim = HubState()
    doc = linked_doc(HubLinkStore(tmp_path), sim)
    sim.rotate_key("policy", payload_override={"add": [], "retire": [sim.policy_keys[-1][0]]})
    applied, error = apply_items(doc, list(sim.rotations))
    assert applied == 0 and error and "laatste sleutel" in error


def test_unsigned_or_tampered_statement_is_refused(tmp_path: Path) -> None:
    sim = HubState()
    doc = linked_doc(HubLinkStore(tmp_path), sim)
    sim.rotate_key("jwt")
    header, payload, sig = sim.rotations[0]["statement"].split(".")
    tampered = {"sequence": 1, "statement": f"{header}.{payload}A.{sig}"}
    applied, error = apply_items(doc, [tampered])
    assert applied == 0 and error
    other = HubState()  # ondertekend door een vreemde sleutel met dezelfde kid
    other.jwt_keys = [(sim.jwt_keys[0][0], other.jwt_keys[0][1])]
    other.rotate_key("jwt")
    assert apply_items(doc, list(other.rotations))[0] == 0


def test_policy_rotation_without_retire_counts_both_policy_keys(tmp_path: Path) -> None:
    """1.3 §4.5: tijdens een policy-rotatie (twee policy-sleutels) telt elke gepinde policy-sleutel mee in de
    vingerafdruk; de dienst rekent hetzelfde als de Hub (simulator, eigen implementatie)."""
    sim = HubState()
    doc = linked_doc(HubLinkStore(tmp_path), sim)
    sim.rotate_key("policy", retire_old=False)
    assert apply_items(doc, list(sim.rotations)) == (1, None)
    assert len(doc["pins"]["policy"]) == 2
    assert doc["pins"]["keyset_fingerprint"] == sim.keyset_fingerprint()
    alleen_nieuwste = keyset_fingerprint_of_thumbprints([e["thumbprint"] for e in doc["pins"]["jwt"]],
                                                        [doc["pins"]["policy"][-1]["thumbprint"]])
    assert doc["pins"]["keyset_fingerprint"] != alleen_nieuwste  # de 1.2-fout (alleen de laatste) is weg


def test_refused_rotation_stays_visible_until_a_later_valid_one(tmp_path: Path) -> None:
    """§3.5 ``key_rotation_blocked_sequence``: een geweigerde rotatie blijft zichtbaar (ook na een herstart)."""
    sim = HubState()
    doc = linked_doc(HubLinkStore(tmp_path), sim)
    goed = HubState(jwt_keys=list(sim.jwt_keys), policy_keys=list(sim.policy_keys))  # dezelfde gepinde sleutels
    sim.rotate_key("jwt", signer=sim.policy_keys[-1])  # ongeldig: verkeerd doel
    assert apply_items(doc, list(sim.rotations))[0] == 0
    assert doc["key_rotation_blocked_sequence"] == 1 and doc["pins"]["rotation_sequence"] == 0
    goed.rotate_key("jwt")
    assert apply_items(doc, list(goed.rotations)) == (1, None)
    assert doc["key_rotation_blocked_sequence"] is None and doc["pins"]["rotation_sequence"] == 1
