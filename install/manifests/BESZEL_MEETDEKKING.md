# Beszel op TrueNAS — wat meet het wel en niet? (I07 stap 1, I07-07-01)

Bron: broncode `henrygd/beszel` tag **v0.21.1** (`e2ed147`, alleen gelezen) en de manifesten
`beszel-hub.manifest.yaml` + `beszel-agent.manifest.yaml`. De agent draait als standaard Custom App **als 568:568,
zonder host network, zonder Docker-socket en zonder GPU-apparaat** (contract §1, §9). Alles hieronder is afgeleid uit
de broncode en het containermodel: **NIET GEVALIDEERD (echte NAS)**. Op de NAS moet stap I07-07-01 dit per regel
bevestigen of corrigeren; tot dan toont de Hub per metriek de bron en `unknown`/`capability_unavailable`, nooit een
schatting (ONTWERPTAAL: "Onbekend — Beszel levert geen …").

| Metriek | Levert Beszel (agent in container)? | Waarom / bron in de code | Alternatieve bron voor de Hub |
|---|---|---|---|
| CPU-gebruik (totaal, per kern, belasting) | **ja, voor de hele NAS** | `/proc/stat` en `/proc/loadavg` tonen in een container de host (`agent/cpu*.go`) | TrueNAS-reporting |
| Geheugen (gebruikt, buffers/cache) | **ja, hele NAS** | `/proc/meminfo` (`agent/memory_linux.go`) | TrueNAS `system.info` |
| ZFS-ARC als geheugen | **waarschijnlijk** | leest `/proc/spl/kstat/zfs/arcstats` (`agent/zfs/zfs_linux.go`); of dat in de container zichtbaar is: onzeker | TrueNAS-reporting (ARC) |
| Temperaturen (CPU, schijven via hwmon) | **gedeeltelijk** | `/sys/class/hwmon` is in een container meestal alleen-lezen zichtbaar (`agent/sensors*.go`); welke sensoren: onzeker | TrueNAS `disk.temperatures` |
| Schijfbezetting van de pools | **nee** (`capability_unavailable`) | de agent meet het rootbestandssysteem van de container (overlay op de apps-dataset), niet de datapools; `zpool list` staat niet in de agent-image (`agent/storage_pool.go`) | TrueNAS `pool.query` / `pool.dataset.query` |
| Netwerkverkeer van de NAS | **nee** | zonder host network ziet de agent alleen de eigen container-interface; de waarde zou misleidend zijn → de Hub negeert hem | TrueNAS-reporting (interfaces) |
| Containers per app (CPU/geheugen) | **nee** | vereist de Docker-socket; die is verboden (contract §1) | TrueNAS `app.query` (status) — geen verbruik per app |
| GPU-gebruik (Intel A310/UHD 770) | **nee** (`SKIP_GPU=true`) | gebruik vereist `intel_gpu_top`/perf-rechten (CAP_PERFMON) of het apparaat; beide niet toegestaan zonder D08-besluit | TrueNAS `app.gpu_choices` (alleen inventaris, D08) |
| SMART | **nee** | vereist `smartctl` met apparaattoegang | TrueNAS `alert.list` (25.10) / `smart.test.results` (25.04) |
| Systemd-diensten | **nee** (`SKIP_SYSTEMD=true`) | geen systemd in de container | — |
| Uptime van de NAS | **ja** | `/proc/uptime` | TrueNAS `system.info` |

Gevolgen:

- **Geen misleidende grafieken (I07-07-04):** de Hub gebruikt van Beszel alleen CPU, geheugen (incl. ARC als die
  aanwezig is), belasting en temperaturen; schijf en netwerk komen van TrueNAS. Beszel en TrueNAS blijven twee reeksen
  per metriek (Hub-alerts: `BRON_VOORKEUR` truenas → hub → beszel → uptime_kuma).
- **Geen out-of-band:** hub en agent draaien op dezelfde NAS (I07 §5). Volledige NAS-uitval meldt Beszel niet; dat
  vraagt een externe host (EX23, NIET GEVALIDEERD).
- **Koppelen (I07-07-03):** SSH-modus. De agent kent alleen de **publieke** sleutel van de hub (`KEY`, parameter uit
  de wizard; de Hub haalt hem op via `GET /api/beszel/info` en laat hem bevestigen). Geen `TOKEN` in het formulier.
- **Geen dubbele host na herstart (I07-07-06):** in SSH-modus is de identiteit van de agent het systeemrecord in de hub
  (adres + poort); de agent schrijft dan **geen** vingerafdruk (`agent/fingerprint.go` wordt alleen door de
  WebSocket-client in `agent/client.go` gebruikt, dus alleen met `TOKEN` + `HUB_URL`). De eigen kleine dataset op
  `/var/lib/beszel-agent` blijft staan als gegevensmap van de agent, maar is in SSH-modus leeg. Gemeten (zie hieronder):
  na een herstart van de agent belt de hub hem zelf weer op en blijft er precies één systeem.

## Gemeten met de echte images (Docker, geen TrueNAS — 2026-11-09)

`tools/upstream_rooktest.py` (`make upstream-rooktest`, CI-job `rooktest`) start de hub en de agent op digest als
568:568 met een lege eigen map, zonder host network, Docker-socket of GPU, en koppelt ze in SSH-modus met alleen de
publieke hubsleutel. Uitkomst op de bouwmachine (Ubuntu-host met ext4, **geen** ZFS en **geen** TrueNAS):

- Hub en agent draaien volledig als 568; de agent toont de SSH-banner `SSH-2.0-beszel_0.21.1`; de hub haalt
  binnen een minuut metingen op (`system_stats` type `1m` met o.a. `cpu`, `m`, `mu`, `mb`, `mem`, `la`, `t`, `ni`).
- Temperaturen (`t`: hwmon/coretemp en NVMe) waren in de container zichtbaar. Of dat op de NAS ook zo is: onzeker.
- Schijf: de agent meet het rootbestandssysteem van de container (`mount=/etc/resolv.conf`), niet de pools — zoals
  hierboven. Netwerk (`ni`): alleen de eigen container-interface `eth0` — zoals hierboven.
- ARC (`mz`) ontbrak; de bouwmachine heeft geen ZFS, dus dit zegt niets over de NAS (blijft **onzeker**).
- Als 568 kan de agent niet in `/tmp` van de image schrijven; hij meldt `SKIP_GPU sensor shadow unavailable` en valt
  terug op filteren na het lezen. Onschadelijk, maar wel een waarschuwing in de app-log.
- Na een herstart van de agent: precies één systeem, weer `up` (gemeten na ca. 60 s; de hub belt op zijn eigen
  minuutritme). De gegevensmap van de agent bleef leeg.

Dit is **Sim-getest** (Docker), geen meting op TrueNAS: **NIET GEVALIDEERD (echte NAS)** blijft staan.
