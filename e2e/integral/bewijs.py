"""Bewijs voor één proef. Alleen bekende protocolvelden verlaten het testnet als bewijs."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from lifecycle.redact import MASK, redact

SIM = {"Sim-geslaagd", "Sim-mislukt"}
PRAKTIJK = {"NIET GEVALIDEERD (echte NAS)", "NIET GEVALIDEERD (hardware)", "NIET GEVALIDEERD (account)",
            "NIET GEVALIDEERD (Mac/Xcode)", "NIET GEVALIDEERD (echte Hub)"}
ROUTES = {"/", "/readyz", "/healthz", "/api/versions", "/_sim/keys", "/_sim/fouten", "/_sim/verzoeken",
          "/_sim/calls", "/api/v1/setup/admin", "/api/v1/decisions/D03", "/api/v1/decisions/D03/waarde",
          "/api/v1/truenas/api-key", "/api/v1/truenas/test", "/api/v1/truenas/tls/trust",
          "/api/v1/services/pairing-codes", "/koppelen", "/api/v1/services/enrollment-tokens", "/api/v1/services"}
APP_IDS = {"ai-server-manager", "ai-server-manager-executor", "jelly-leon-platform"}
SIM_API = "v25.10.0"
WAARDEN = APP_IDS | {SIM_API} | {"ok", "ready", "active", "not_ready", "tls_untrusted", "gekoppeld"}


def protocolwaarde(data: Any) -> Any:
    """Positieve selectie: ook onbekende korte geheimen in vrije tekst, sleutels of lijsten vallen weg."""
    data = redact(data)
    if isinstance(data, dict):
        velden = {"status", "verbonden", "gekoppeld", "app_id", "reason_code", "vertrouwd", "items", "ingesteld"}
        return {k: protocolwaarde(v) for k, v in data.items() if k in velden}
    if isinstance(data, list):
        return [protocolwaarde(item) for item in data[:100]]
    if isinstance(data, bool) or data is None:
        return data
    return data if isinstance(data, str) and data in WAARDEN else MASK


def uitwisseling(dienst: str, methode: str, url: str, verzoek: Any, http: int, antwoord: Any) -> dict[str, Any]:
    pad = urlsplit(url).path
    methode = methode if methode in {"GET", "HEAD", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"} else MASK
    return {"dienst": dienst, "methode": methode, "pad": pad if pad in ROUTES else MASK,
            "verzoek": protocolwaarde(verzoek), "http": http, "antwoord": protocolwaarde(antwoord)}


def schrijf(pad: Path, gegevens: Any) -> None:
    # Een lezer ziet óf het vorige óf het complete nieuwe dossier, ook bij afbreken.
    tmp = pad.with_suffix(".nieuw")
    tmp.write_text(json.dumps(redact(gegevens), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(pad)


def praktijkblokkade(tekst: str) -> bool:
    delen = tekst.split("; ")
    return set(delen) <= PRAKTIJK and len(delen) == len(set(delen))


def controleer(rij: dict[str, str], soort: str) -> None:
    validatie = rij["validatie"].split("; ")
    if (soort == "Sim" and rij["status"] == "Bezig" and len(validatie) == 1
            and validatie[0] in SIM and not rij["blokkade"]):
        return
    praktijk = rij["blokkade"].split("; ")
    if soort == "Sim+" and validatie[-1] in SIM:
        validatie = validatie[:-1]
    if (soort in {"Sim+", "Echt"} and rij["status"] == "Geblokkeerd" and praktijkblokkade(rij["blokkade"])
            and rij["opmerking"].strip() and validatie == praktijk):
        return
    raise ValueError("Ongeldige combinatie van status, validatie en blokkade in EVIDENCE-voorstel")


def voorstel(scenario: Any, uitkomst: str | None, datum: str, bestand: str) -> dict[str, str]:
    praktijk = scenario.soort != "Sim"
    validatie = scenario.blokkade.split("; ") if praktijk else []
    if uitkomst is not None:
        validatie.append(uitkomst)
    rij = {"id": scenario.id, "repo": "jelly-leon-platform", "bijgewerkt": datum, "bewijs": bestand,
           "status": "Geblokkeerd" if praktijk else "Bezig", "validatie": "; ".join(validatie),
           "blokkade": scenario.blokkade, "opmerking": scenario.toelichting}
    controleer(rij, scenario.soort)
    return rij
