"""Bevindingen van de validator: machineleesbare code + Nederlandse uitleg."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from enum import StrEnum

from pydantic import BaseModel, ConfigDict


class Severity(StrEnum):
    ERROR = "error"
    WARNING = "warning"


class Finding(BaseModel):
    model_config = ConfigDict(frozen=True)

    code: str
    severity: Severity
    path: str
    message: str
    #: contract 1.3 §9: waarschuwing uit de vaste lijst die het manifest in ``accepted_findings`` accepteert
    accepted: bool = False
    accepted_reden_nl: str | None = None

    def as_text(self) -> str:
        label = "FOUT" if self.severity is Severity.ERROR else ("GEACC." if self.accepted else "LET OP")
        return f"{label:<7}{self.path:<40} {self.code}: {self.message}"


#: ADR-0021: een pad in een melding is hooguit zoveel tekens. Een langer pad (veel geneste sleutels van elk hooguit 256
#: tekens) staat er ingekort: het begin en :data:`PATH_CUT`. Zo is één melding begrensd; het aantal meldingen per rapport
#: begrenst :data:`MAX_FINDINGS` (ADR-0022). De paden van de bestaande manifesten zijn hooguit enkele tientallen tekens.
MAX_SHOWN_PATH = 1024
PATH_CUT = "…"


def _shown(path: str) -> str:
    if len(path) > MAX_SHOWN_PATH:
        return path[:MAX_SHOWN_PATH] + PATH_CUT
    return path or "(root)"


#: ADR-0022 (besluit B-1): een rapport verzamelt hooguit zoveel meldingen. Komt er nog een bij, dan stopt het verzamelen
#: en krijgt het rapport één vaste fout :data:`TOO_MANY` (fail-closed: het rapport is ongeldig, de vertaling heeft geen
#: payload). Het geldt per rapport: de validatie (``validate_data``, ook via ``validate_text``, het JSON-veld en
#: ``manifest_yaml`` van ``POST /catalog/v1/validate`` en de synchronisatie), de vertaling (``translate``, ook in
#: ``POST /catalog/v1/preview``) en de conflictanalyse van de preview. De bestaande manifesten geven hooguit 23
#: meldingen; één verzoek van 256 KiB gaf er 126 112 (antwoord 282 MiB, RSS tot 2,8 GiB).
MAX_FINDINGS = 1000
TOO_MANY = "te_veel_meldingen"


class TooManyFindingsError(Exception):
    """Er komt een melding bij boven :data:`MAX_FINDINGS`; het rapport dat :func:`finding_budget` opende, stopt."""


class FindingBudget:
    """De meldingen van één rapport: hoeveel er zijn gemaakt, en de eerste :data:`MAX_FINDINGS` zelf."""

    __slots__ = ("count", "kept")

    def __init__(self) -> None:
        self.count = 0
        self.kept: list[Finding] = []

    def result(self) -> list[Finding]:
        """De eerste :data:`MAX_FINDINGS` meldingen en daarna de vaste fout :data:`TOO_MANY`."""
        return [*self.kept, _too_many()]


_BUDGET: ContextVar[FindingBudget | None] = ContextVar("appstore_finding_budget", default=None)


@contextmanager
def finding_budget() -> Iterator[FindingBudget]:
    """Een nieuw rapport (ADR-0022): elke melding die :func:`error` of :func:`warning` binnen dit blok maakt, telt mee.
    Bij melding nummer ``MAX_FINDINGS + 1`` gooit die functie :class:`TooManyFindingsError`; de aanroeper vangt die en
    geeft :meth:`FindingBudget.result` als meldingen, zonder payload. Zo groeit het geheugen niet met het aantal
    meldingen. Een geneste aanroep opent een eigen budget; na het blok geldt weer het vorige (of geen)."""
    budget = FindingBudget()
    token = _BUDGET.set(budget)
    try:
        yield budget
    finally:
        _BUDGET.reset(token)


def _counted(finding: Finding) -> Finding:
    budget = _BUDGET.get()
    if budget is not None:
        budget.count += 1
        if budget.count > MAX_FINDINGS:
            raise TooManyFindingsError
        budget.kept.append(finding)
    return finding


def _too_many() -> Finding:
    # niet via error(): de vaste fout telt zelf niet mee
    return Finding(
        code=TOO_MANY,
        severity=Severity.ERROR,
        path=_shown(""),
        message=(
            f"Meer dan {MAX_FINDINGS} meldingen: het verzamelen is gestopt en het rapport is ongeldig (fail-closed, "
            "ADR-0022). Het rapport toont alleen de eerste meldingen; los die op en valideer opnieuw."
        ),
    )


def error(code: str, path: str, message: str) -> Finding:
    return _counted(Finding(code=code, severity=Severity.ERROR, path=_shown(path), message=message))


def warning(code: str, path: str, message: str) -> Finding:
    return _counted(Finding(code=code, severity=Severity.WARNING, path=_shown(path), message=message))


#: Alle codes die de validator kan geven (documentatie + test op volledigheid).
CODES: dict[str, str] = {
    # invoer
    "yaml_invalid": "Het bestand is geen geldige YAML.",
    "yaml_alias_forbidden": "YAML-ankers/aliassen zijn niet toegestaan.",
    "yaml_duplicate_key": "Een sleutel komt dubbel voor.",
    "yaml_custom_tag_forbidden": "Eigen YAML-tags zijn niet toegestaan.",
    "yaml_merge_key_forbidden": "YAML-merge-sleutels (<<) zijn niet toegestaan.",
    "yaml_key_not_string": "Een sleutel moet tekst zijn.",
    "yaml_too_deep": "Het manifest is te diep genest.",
    "manifest_too_large": "Het manifest is te groot.",
    "manifest_not_mapping": "Het manifest moet een object (mapping) zijn.",
    # schema
    "required_field_missing": "Verplicht veld ontbreekt.",
    "unknown_field": "Onbekend veld.",
    "wrong_type": "Verkeerd type.",
    "invalid_value": "Ongeldige waarde.",
    "invalid_format": "Ongeldig formaat.",
    "out_of_range": "Waarde buiten het toegestane bereik.",
    "forbidden_combination": "Deze velden mogen niet samen voorkomen.",
    "model_mismatch": "Intern: schema en model oordelen verschillend.",
    "deprecated_field": "Vervallen veld uit een eerdere contractversie (waarschuwing; fout in --strict).",
    # installatiemethode
    "compose_forbidden": "Compose/YAML-installatie is verboden.",
    "install_method_forbidden": "Installatiemethode is niet toegestaan.",
    "adoption_manifest_not_installable": "Adoptiemanifest van een bestaande app: alleen inventaris, nooit installeren.",
    "checklist_not_installable": "Controlelijst (kind: checklist) zonder installatie: eigen schema, nooit installeren.",
    "install_reason_ontbreekt": "Catalogusapp zonder install.reason (waarschuwing; fout in --strict).",
    "restrisico_nnp_ontbreekt": "Upstream-image als Custom App noemt het restrisico zonder no-new-privileges niet.",
    # contract 1.3 §9: geaccepteerde bevindingen
    "accepted_finding_unused": "Geaccepteerde code treedt niet (meer) op; haal hem uit accepted_findings.",
    # image
    "tag_missing": "Image-tag ontbreekt.",
    "mutable_tag_forbidden": "Veranderlijke tag (zoals latest) is verboden.",
    "tag_in_repository": "Tag of digest hoort niet in het repository-veld.",
    "digest_missing": "Digest ontbreekt voor een upstream-image.",
    "digest_pending_release": "Digest van eigen image wordt door de release-CI ingevuld.",
    "imprecise_version_tag": "Tag lijkt geen exacte versie.",
    "mutable_version_forbidden": "Catalogusversie moet exact zijn.",
    # privileges
    "privileged_forbidden": "Privileged-modus is verboden.",
    "docker_socket_forbidden": "Docker-socket in een container is verboden.",
    "capability_forbidden": "Deze Linux-capability is verboden.",
    "capability_without_reason": "Extra capability zonder privilege_reason.",
    "capability_unknown": "Onbekende Linux-capability.",
    "device_forbidden": "Dit apparaat mag niet aan een container worden gegeven.",
    "device_without_reason": "Apparaat zonder privilege_reason.",
    "gpu_exceeds_privileges": "custom_app.gpu vraagt meer GPU dan privileges.gpu toestaat.",
    "gpu_without_reason": "GPU-toegang zonder privilege_reason.",
    "gpu_decision_missing": "GPU-toegang zonder D08 in decisions_required.",
    "gpu_param_invalid": "GPU-parameter moet uit de wizard komen, met D08 en niet verplicht.",
    "run_as_image_default_upstream": "Upstream-image draait als de standaardgebruiker van de image.",
    # netwerk
    "host_network_without_reason": "Host network zonder host_network_reason.",
    "host_network_requested": "Host network gevraagd.",
    "platform_network_not_supported": "Apps zitten niet in een gedeeld Docker-netwerk.",
    "ports_inconsistent": "Vervallen custom_app.ports wijkt af van network.ports.",
    "duplicate_port": "Poort komt dubbel voor.",
    "port_privileged_range": "Hostpoort onder 1024.",
    # opslag
    "literal_host_path": "Hostpad moet uit de datasetkiezer komen.",
    "forbidden_host_path": "Hostpad is verboden.",
    "forbidden_mount_path": "Koppelpad in de container is verboden.",
    "dataset_role_missing": "Opslag zonder datasetrol.",
    "dataset_role_inconsistent": "Datasetrol van parameter en opslag verschilt.",
    "storage_unknown_reference": "Opslag verwijst naar onbekende naam.",
    "storage_not_mapped": "Gedeclareerde opslag heeft geen formulierregel.",
    "duplicate_name": "Naam komt dubbel voor.",
    "host_path_param_wrong_source": "Hostpad-parameter moet uit de datasetkiezer komen.",
    # parameters
    "unknown_parameter": "Verwijzing naar onbekende parameter.",
    "parameter_type_mismatch": "Parameter heeft het verkeerde type voor dit veld.",
    "parameter_default_missing": "Parameter met source 'default' zonder default.",
    "parameter_default_not_allowed": "Default is niet toegestaan bij deze source.",
    "parameter_unused": "Parameter wordt nergens gebruikt.",
    "example_value_as_real": "Voorbeeldwaarde gebruikt als echte waarde.",
    "secret_literal_forbidden": "Geheim als letterlijke waarde in het manifest.",
    "secret_in_form": "Geheim zou in het Custom App-formulier terechtkomen.",
    "account_gegevens_verboden": "Aanmelding van een beheerder of dienstaccount, wachtwoordhash, geheime sleutel of gebruikerslijst in het manifest of de vertaling.",
    "secret_param_invalid": "secret: true alleen bij source generated (de Hub maakt hem) of secret.",
    "reserved_env": "HUB_* is gereserveerd voor het koppelen en geen parameter of omgevingsvariabele.",
    "interpolation_forbidden": "Variabele-interpolatie ($VAR, ${…}, $(…)) is niet toegestaan.",
    "decision_not_listed": "Besluit ontbreekt in decisions_required.",
    # accounts (aanvulling C-12, ADR-0016 W1, ADR-0023)
    "account_ongeldig": "Account met een combinatie die de standaard verbiedt (rol, aanmaak, afnemer, rechten, beheerder).",
    "account_controle_ontbreekt": "Account noemt een controle die niet in post_install_checks staat.",
    "account_controle_te_lang": "Controle die een account noemt, heeft een beschrijving van meer dan 500 tekens.",
    # beheer
    "risk_below_policy_minimum": "Risicoklasse lager dan het platformminimum.",
    "tag_only_rollback_with_migrations": "Tag-only rollback met databasemigraties is onveilig.",
    "backup_unknown_dataset": "Back-up verwijst naar onbekende opslag.",
    "backup_dataset_not_covered": "Data-opslag zit niet in de snapshotlijst.",
    "backup_db_export_none": "Geen database-export opgegeven.",
    "stopped_snapshot_without_dataset": "stopped_snapshot zonder dataset in snapshot_datasets.",
    "healthcheck_unknown_port": "Healthcheck verwijst naar onbekende poort.",
    "healthcheck_monitor_missing": "Geen monitorplan (healthcheck.monitor) voor Uptime Kuma.",
    "same_as_host_not_allowed": "container_port same_as_host alleen bij een catalogusapp.",
    "portal_unknown_port": "Portal verwijst naar onbekende poort.",
    "dependency_self": "App hangt van zichzelf af.",
    "compatibility_missing": "Geen TrueNAS-compatibiliteit opgegeven.",
    "validation_status_claim_ignored": "Geclaimde validatiestatus wordt niet overgenomen.",
    "timezone_missing": "Tijdzone ontbreekt.",
    "catalog_value_forbidden": "Verboden instelling in de waarden van een catalogusapp.",
    "signature_missing": "Handtekening ontbreekt terwijl de bron die vereist.",
    "signature_invalid": "Handtekening klopt niet.",
    "app_id_claimed_by_other_source": "App-ID hoort bij een andere catalogusbron.",
    "manifest_niet_verwerkt": "Een onverwachte fout bij dit manifest: alleen dit manifest is geweigerd (fail-closed).",
    "te_veel_meldingen": "Meer dan 1000 meldingen: het verzamelen is gestopt en het rapport is ongeldig (fail-closed).",
    # vertaling naar TrueNAS (app.create)
    "parameter_value_missing": "Waarde voor een verplichte parameter ontbreekt.",
    "parameter_value_invalid": "Waarde voor een parameter is ongeldig.",
    "parameter_value_unknown": "Waarde opgegeven voor een onbekende parameter.",
    "parameter_value_not_allowed": "Deze parameter mag niet via de installatie worden meegegeven.",
    "vault_ref_not_allowed": "Kluisverwijzing in het manifest, of een vault-verwijzing die de vertaling niet zelf maakte.",
    "manifest_invalid": "Het manifest is niet geldig; vertalen is geweigerd.",
    "vertaling_te_groot": "De vertaling is te groot (te veel ingevulde plekken of een te grote payload); geweigerd (fail-closed).",
    "ix_app_version_unknown": "Versie van de TrueNAS-catalogusapp ix-app is onbekend.",
    "ix_app_version_unsupported": "Versie van ix-app wordt niet ondersteund.",
    "ix_app_version_unverified": "Waardenstructuur van deze ix-app-versie is niet geverifieerd.",
    "truenas_version_unknown": "TrueNAS-versie is onbekend (D01).",
    "truenas_version_incompatible": "App is niet compatibel met deze TrueNAS-versie.",
    "truenas_version_invalid": "TrueNAS-versie heeft een onbekend formaat.",
    "digest_not_pinned": "Image zonder digest; installatie zou een veranderlijke tag gebruiken.",
    "version_unavailable": "De vastgezette versie staat niet (meer) in de gemeten TrueNAS-catalogus.",
    "version_availability_unknown": "Niet gemeten welke versies de TrueNAS-catalogus nog heeft.",
    "gpu_not_measured": "GPU gevraagd, maar D08 heeft geen render-node gemeten.",
    "gpu_all_render_devices": "De app krijgt elke render-GPU van de NAS (het formulier kan niet per GPU kiezen).",
    # conflictanalyse
    "inventory_missing": "Geen gemeten TrueNAS-inventaris; conflicten zijn onbekend.",
    "inventory_stale": "TrueNAS-inventaris is verouderd.",
    "app_name_taken": "Er bestaat al een app met deze naam.",
    "port_in_use": "Hostpoort is al in gebruik.",
    "port_reserved_platform": "Hostpoort is gereserveerd voor een platform-app.",
    "host_path_in_use": "Hostpad wordt al door een andere app gebruikt.",
    "host_path_overlap": "Hostpad overlapt met dat van een andere app.",
    "dependency_missing": "Vereiste afhankelijkheid is niet geïnstalleerd.",
    "dependency_optional_missing": "Optionele afhankelijkheid is niet geïnstalleerd.",
    "dependency_version_unknown": "Versie van een afhankelijkheid is onbekend.",
    "dependency_version_too_low": "Versie van een afhankelijkheid is te laag.",
}
