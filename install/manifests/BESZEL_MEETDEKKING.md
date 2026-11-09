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
- **Geen dubbele host na herstart (I07-07-06):** de agent bewaart zijn vingerafdruk in `/var/lib/beszel-agent`
  (eigen kleine dataset). Zonder die dataset krijgt de agent na elke herinstallatie een nieuwe identiteit.
