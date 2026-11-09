# ADR-0015 — I01 en I02 als controlelijst (eigen formaat `checklist v1`)

- Status: aanvaard · Datum: 2026-11-09 · Contract: 1.2 §1 (geen terminal), §9, §11 · Excel: I01-01-01…08,
  I02-02-01…08

## Aanleiding

De opdracht vraagt I01 (TrueNAS-basis) en I02 (OneDrive, ZFS-snapshots en herstel) "als checklist-manifests (of eigen
formaat als de standaard dat niet kent)". De manifeststandaard (contract §9, schema van appstore-en-fork) kent alleen
`install.method: custom_app | catalog_app`: er is geen soort "controlelijst zonder installatie". I01 en I02 installeren
niets; ze leggen de NAS vast, richten taken in de TrueNAS-web-UI in en bewijzen herstel.

## Besluit

1. **Eigen formaat** `checklist_version: "1"` met JSON Schema `schemas/checklist-v1.schema.json` en regels in
   `tools/checklists.py`; bestanden in `install/checklists/I0x_….checklist.yaml`.
2. **Installeert en wijzigt niets**: `installatie: geen` en `mutaties_door_platform: false` zijn constanten in het
   schema. TrueNAS-methoden in een controlelijst moeten **alleen-lezen** zijn (laatste segment in een vaste lijst
   zoals `query`, `info`, `version`, `used_ports`, `gpu_choices`; nooit `create/update/delete/…`), per TrueNAS-versie
   (`zfs.snapshot.query` alleen 25.04, `pool.snapshot.query` alleen 25.10, `smart.test.results` alleen 25.04).
   Wijzigingen doet de gebruiker in de TrueNAS-web-UI (menupad verplicht) of ze volgen later als goedgekeurde R1/R2-
   actie via de executor.
3. **Traceerbaar naar de Excel**: per stap exact de Excel-ID, -titel, -acceptatie en -risico (de CI faalt bij
   afwijking); `uitvoeren_nl` mag afwijken van het I-document, maar dan met `afwijking_van_document_nl`.
4. **Geen terminal**: I01 stap 3 noemt shellopdrachten voor `/dev/dri`; die staan niet in de lijst. GPU-inventaris komt
   uit `app.gpu_choices`, de koppeling render-node ↔ kaart is een bewijsrecord (D08).
5. **Eerlijke validatie per stap**: nooit alleen `Sim-getest`; OneDrive-stappen zijn `NIET GEVALIDEERD (account)`,
   telefoon/GPU `NIET GEVALIDEERD (hardware)`, de rest `NIET GEVALIDEERD (echte NAS)`.
6. **Simulatortest**: elke genoemde methode wordt met de alleen-lezen-key van de Hub tegen de TrueNAS-simulator
   (25.04.2.6 en 25.10.8) aangeroepen; methoden van de andere versie moeten daar echt `-32601` geven.

## Gevolgen

- De Hub kan de lijsten later inlezen (wizard/gates); de uitkomsten horen als meting of bewijsrecord bij D01–D08/D16.
- Contract-issue: de manifeststandaard (eigenaar appstore-en-fork) kent geen controlelijsten. Voorstel: een soort
  `install.method: none` met `checklist`, of dit formaat overnemen in de standaard.
