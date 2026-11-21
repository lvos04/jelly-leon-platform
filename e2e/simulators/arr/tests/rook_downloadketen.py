"""Start de vier ingangen in één geïsoleerde MSO-testcontainer en volg één synthetische NZB."""

import os
import subprocess
import sys
import time
from contextlib import ExitStack

import httpx


def stop(proc):
    proc.terminate()
    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait()


def start(stack, client, module, port, *args, env=None):
    proc = subprocess.Popen([sys.executable, "-m", module, "--host", "intern", *args], env=env)
    stack.callback(stop, proc)
    base = f"http://localhost:{port}"
    deadline = time.monotonic() + 15
    while proc.poll() is None and time.monotonic() < deadline:
        try:
            keys = client.get(base + "/_sim/sleutels").raise_for_status().json()
            return base, keys
        except httpx.ConnectError:
            time.sleep(0.05)
    raise RuntimeError(f"De startproef voor {module} op {port} kreeg geen testsleutels")


def main():
    with ExitStack() as stack, httpx.Client(trust_env=False) as client:
        sab, sab_keys = start(stack, client, "sabnzbd", 30217)
        arr, keys = start(stack, client, "arr", 30216, "--sab-url", sab,
                          env={**os.environ, "SIM_SAB_APIKEY": sab_keys["apikey"]})
        qb, login = start(stack, client, "qbittorrent", 30218)
        prowlarr, pro_keys = start(stack, client, "arr", 30219, "--prowlarr")
        assert client.post(qb + "/api/v2/auth/login", data=login).text == "Ok."
        assert client.get(qb + "/api/v2/app/preferences").raise_for_status().json()["dht"] is True
        assert client.get(prowlarr + "/api/v1/indexer", headers={"X-Api-Key": pro_keys["apikey"]}).json()
        request = client.post(arr + "/api/v1/request", headers={"X-Api-Key": keys["seerr"]},
                              json={"mediaType": "tv", "mediaId": 101, "seasons": [1]}).raise_for_status().json()
        root = arr + "/sonarr-series/api/v3"
        headers = {"X-Api-Key": keys["sonarr-series"]}
        sid = request["media"]["externalServiceId"]
        episode = client.get(root + "/episode", headers=headers, params={"seriesId": sid}).json()[0]
        command = client.post(root + "/command", headers=headers, json={"name": "EpisodeSearch",
                              "episodeIds": [episode["id"]]}).raise_for_status().json()
        assert client.get(f"{root}/command/{command['id']}", headers=headers).json()["status"] == "completed"
        queue = client.get(root + "/queue", headers=headers).json()["records"]
        params = {"mode": "queue", "apikey": sab_keys["apikey"]}
        slots = client.get(sab + "/api", params=params).json()["queue"]["slots"]
        assert len(queue) == 1 and queue[0]["downloadId"] in {s["nzo_id"] for s in slots}
    print("Vier startmodules bereikbaar; Sonarr en SAB tonen dezelfde download-ID.")


if __name__ == "__main__":
    main()
