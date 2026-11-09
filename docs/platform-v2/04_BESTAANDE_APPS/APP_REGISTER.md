# Bestaande apps — beslisregister

Niet iedere huidige app moet worden herbouwd. De Hub importeert alle apps als catalogusitem en laat eigenaar, status en last-used zien. Stop of verwijder bestaande apps alleen met bewijs, expliciete instemming, eigen backup en een herstelperiode.

| App | Voorgenomen besluit | Belangrijkste controle |
|---|---|---|
| Plex | Kandidaat uitschakelen na bewezen Jellyfin parity | Test alle gebruikte players, subtitle profiles en gebruikers; stop Plex 72u, rollback bij missen functies. |
| Emby | Kandidaat uitschakelen na Jellyfin/MediaGuard audit | Onderzoek refresh/playback/bibliotheek; switch alleen na zelfde resultaat in Jellyfin. |
| JellyPlex-Watched | Kandidaat uitschakelen als synchronisatie overbodig | Test watch/resume state van minstens 3 gebruikers voor/na stop. |
| Readarr | Alleen behouden indien huidige taak nog actief | Meet traffic/ingest en gebruik; disable zonder data deletion en rollback. |
| FlareSolverr | Alleen behouden indien Prowlarr/indexers het nodig hebben | Geen indexer mag stuk na test disable. |
| Snapotter | Behouden eigen dienst | Admin/gebruikers/DB/containers correct en Hub only read link. |
| Teeltboek | Behouden eigen dienst | Inloggen, export en database herstelscenario getest. |
| Uren | Behouden eigen dienst | Inloggen, exports, appbackups en gebruikersrechten getest. |
| Bazarr | Behouden tot subtitle parity bewezen | SRT/ASS anime/film test en fallback aanwezig. |
| Immich | Behouden, primaire fotoapp | Test thumbnails/albums plus originele foto en DB herstel. |
| Prowlarr | Behouden, eigenaar indexercatalogus | Geen cyclische zoeklus of tag drift. |
| Seerr | Behouden, verzoekfrontend | Vier arr apps en aanvraagstatus correct. |
| SABnzbd | Behouden, Usenet client | Geen wildcard category misrouting. |
| qBittorrent | Behouden, torrentclient | Geen automatische bestanden verwijderen. |
| Tailscale | Behouden, privé toegang | Niet publiek toegankelijk zonder VPN. |
| Cloudflared | Behouden voor geselecteerde app toegang | Tunnel health + token leakage test. |
| Jellyfin | Behouden als media server en toekomstig fork fundament | No library/watch state regression. |
| MediaGuard | Behouden, bibliotheek- en AV1-eigenaar | Geteste noodstop, HDR/VMAF failclosed. |
| Media Source Optimizer | Behouden, bronoptimalisatie | Echte service mocks/live parity en geen owner writes. |
| my_house | Bouwen/testen en integreren (release v0.1.0) | Boiler nooit schakelen; Gree readback. |

**Bouwvoorwaarde:** eigenaar en werkelijke versie/route/media mount uit TrueNAS inventaris, niet uit screenshot alleen. In `03_INSTALLATIE/I26_BESTAANDE_APPS.md` is een geteste decommission-procedure beschreven.
