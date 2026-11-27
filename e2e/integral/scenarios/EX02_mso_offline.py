"""EX02 · MediaGuard en MSO. Een nieuwe zoekopdracht bereikt SAB terwijl MSO gestopt is."""

from e2e.integral.bewijs import schrijf
from e2e.integral.downloadketen import Downloadketen
from e2e.integral.media import behoud, draait, inloggen, plan_werk, uitval, verbind_mso
from e2e.integral.scenarios.conftest import Kern, wacht

SCENARIO = {
    "eigenaar": "MediaGuard en MSO", "groepen": ("media", "uitval"),
    "diensten": ("mediaguard", "mso", "arr-sim", "sabnzbd-sim"),
    "verwachting": "Tijdens MSO-uitval verschijnt de door MediaGuard gezochte aflevering met dezelfde nieuwe ID "
                   "in de wachtrijen van arr en SAB.",
}


def test_mso_offline(kern: Kern) -> None:
    mg, mso = inloggen(kern, "mediaguard"), inloggen(kern, "mso")
    verbind_mso(mso, mg)

    def verbonden() -> bool:
        status = mso.get("/api/v1/mediaguard/status", params={"refresh": True}).raise_for_status().json()
        return bool(status["mode"] == "connected")

    wacht("verbonden MSO vóór uitval", verbonden)
    keten = Downloadketen(kern, mg)
    with behoud(kern, mg, plan_werk(kern)) as meet:
        voor = keten.sab_ids() | keten.aflevering_ids()
        with uitval(kern, "mso"):
            gestopt_voor = not draait(kern, "mso")
            resultaat = mg.post(f"/api/v1/wanted/{keten.wanted}/search").raise_for_status().json()
            wacht("nieuwe download van de gezochte aflevering in arr en SAB",
                  lambda: bool((keten.aflevering_ids() & keten.sab_ids()) - voor))
            gedeeld = (keten.aflevering_ids() & keten.sab_ids()) - voor
            gestopt_na = not draait(kern, "mso")
            meet("tijdens", False)
        wacht("verbonden MSO na herstel", verbonden)
        schrijf(kern.net.dossier / "download.json", {
            "mso_verbonden_voor_en_na": True, "gestopt_voor_zoek": gestopt_voor, "gestopt_na_download": gestopt_na,
            "zoekopdrachten": resultaat["issued"], "nieuwe_gedeelde_download_ids": len(gedeeld),
            "nieuwe_sab_taken": len(keten.sab_ids() - voor),
        })
        assert resultaat["issued"] == 1 and len(gedeeld) == 1
        assert gestopt_voor and gestopt_na, "MSO moet tijdens zoeken en downloaden gestopt zijn"
