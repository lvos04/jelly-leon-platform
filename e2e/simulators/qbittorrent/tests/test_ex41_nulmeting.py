"""Een bijwerking van de eerste MSO-koppeling moet de private proef rood maken."""

import json
from types import SimpleNamespace
from unittest.mock import Mock

import httpx
import pytest
from fastapi.testclient import TestClient
from qbittorrent_sim import create_app

from e2e.integral.scenarios import EX41_private_torrent as ex41


@pytest.mark.parametrize("wijzig_bij_koppelen", [False, True])
def test_nulmeting_voor_de_eerste_koppeling(mso_bron, monkeypatch, tmp_path, wijzig_bij_koppelen):
    with TestClient(create_app(leeg=True)) as publiek, TestClient(create_app()) as prive:
        kern = SimpleNamespace(client=lambda dienst, _: publiek if dienst == "qbittorrent-publiek" else prive,
                               verzoeklog=lambda sim: sim.get("/_sim/verzoeken").json(),
                               net=SimpleNamespace(dossier=tmp_path))
        actief = None

        def koppel(_api, _soort, qb, _login):
            nonlocal actief
            actief = qb
            if qb is prive and wijzig_bij_koppelen:
                torrent, = [t for t in qb.get("/api/v2/torrents/info").json()
                            if qb.get("/api/v2/torrents/properties", params={"hash": t["hash"]}).json()["is_private"]]
                qb.post("/api/v2/torrents/setShareLimits", data={
                    "hashes": torrent["hash"], "ratioLimit": 2.5, "seedingTimeLimit": 60,
                    "inactiveSeedingTimeLimit": 17,
                }).raise_for_status()

        def post(route, *, json):
            code, body = 200, {"id": 1}
            if route.endswith("/preview"):
                if actief is prive:
                    code, body = 422, {"detail": "private seedverplichting"}
                else:
                    body = {"ops": [{"path": "pref:dht"}, {"path": "pref:pex"}]}
            return httpx.Response(code, json=body, request=httpx.Request("POST", "http://proef.invalid"))

        monkeypatch.setattr(ex41, "eerste_start", lambda *_: SimpleNamespace(post=post))
        monkeypatch.setattr(ex41, "herken", Mock())
        monkeypatch.setattr(ex41, "koppel", koppel)
        monkeypatch.setattr(ex41, "toepassen", lambda *_: publiek.post("/api/v2/app/setPreferences",
                            data={"json": json.dumps({"dht": True, "pex": True})}).raise_for_status())
        if wijzig_bij_koppelen:
            with pytest.raises(AssertionError, match="private torrent"):
                ex41.test_private_en_publieke_torrent(kern)
        else:
            ex41.test_private_en_publieke_torrent(kern)
            assert json.loads((tmp_path / "mso.json").read_text())["prive_schrijfacties"] == []
