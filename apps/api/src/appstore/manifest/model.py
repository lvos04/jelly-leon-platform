"""Getypt model van manifest v1 (spiegel van ``manifest-v1.schema.json``).

Het JSON Schema is normatief (taalneutraal, ook bruikbaar in andere repos); dit pydantic-model wordt alleen
gebouwd voor manifesten die het schema én de veiligheidsregels doorstaan, zodat de vertaling naar TrueNAS
met getypte velden werkt in plaats van met losse dicts. ``tests/unit/test_manifest_schema.py`` bewaakt dat
schema en model gelijk oordelen.
"""

from __future__ import annotations

from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field

NAME_PATTERN = r"^[a-z][a-z0-9_-]{0,62}$"
PARAM_PATTERN = r"^[a-z][a-z0-9_]{0,62}$"
APP_ID_PATTERN = r"^[a-z]([-a-z0-9]*[a-z0-9])?$"

Name = Annotated[str, Field(pattern=NAME_PATTERN)]
PortNumber = Annotated[int, Field(ge=1, le=65535)]
RiskClass = Literal["R0", "R1", "R2", "R3"]


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class ParamRef(_Strict):
    """Getypte verwijzing naar een parameter. Er bestaat bewust géén string-templating (``${...}``)."""

    param: Annotated[str, Field(pattern=PARAM_PATTERN)]


class Install(_Strict):
    method: Literal["custom_app", "catalog_app"]
    #: contract 1.3 §9: waarom een catalogusapp ('meerdere_containers' of een NL-reden)
    reason: str | None = None


class Image(_Strict):
    repository: str
    tag: str
    digest: str | None

    def reference(self, *, with_digest: bool = True) -> str:
        ref = f"{self.repository}:{self.tag}"
        if with_digest and self.digest:
            ref += f"@{self.digest}"
        return ref


SAME_AS_HOST = "same_as_host"


class PortBinding(_Strict):
    name: Name
    #: ``same_as_host`` alleen bij catalogusapps waarvan de container op de gekozen hostpoort luistert (1.3 §9)
    container_port: PortNumber | Literal["same_as_host"]
    host_port: PortNumber | ParamRef
    protocol: Literal["tcp", "udp"]
    purpose_nl: str | None = None


class StorageBinding(_Strict):
    name: Name
    type: Literal["host_path", "ix_volume", "tmpfs"]
    host_path: str | ParamRef | None = None
    dataset_name: Name | None = None
    tmpfs_size_mib: Annotated[int, Field(ge=1, le=65536)] | None = None
    read_only: bool = False


class EnvVar(_Strict):
    name: Annotated[str, Field(pattern=r"^[A-Za-z_][A-Za-z0-9_]{0,127}$")]
    value: str | None = None
    value_from: ParamRef | None = None


class Resources(_Strict):
    cpus: Annotated[int, Field(ge=1, le=256)] | None
    memory_mib: Annotated[int, Field(ge=64, le=1048576)] | None


class RunAs(_Strict):
    mode: Literal["image_default", "custom"]
    user: Annotated[int, Field(ge=1)] | None = None
    group: Annotated[int, Field(ge=1)] | None = None


class Portal(_Strict):
    port: Name
    scheme: Literal["http", "https"] = "http"
    path: str = "/"
    name: str = "Web UI"


GpuChoice = Literal["none", "intel_render"]


class CustomApp(_Strict):
    #: Vervallen in contract 1.2 §9 (poorten alleen in ``network.ports``); alleen voor de overgang toegestaan.
    ports: list[PortBinding] | None = None
    storage: list[StorageBinding]
    env: list[EnvVar]
    resources: Resources
    #: ``none``, ``intel_render`` of een verwijzing naar een parameter van type ``gpu`` (contract 1.2 §9).
    gpu: GpuChoice | ParamRef
    run_as: RunAs
    timezone: str | ParamRef | None = None
    restart_policy: Literal["unless-stopped", "on-failure", "always", "no"] = "unless-stopped"
    portal: Portal | None = None


class Catalog(_Strict):
    train: Literal["stable", "community", "enterprise"]
    app: str
    version: str


ParamType = Literal[
    "string", "integer", "boolean", "port", "path", "dataset", "url", "timezone", "duration", "secret_ref", "gpu"
]
ParamSource = Literal["wizard", "dataset_picker", "secret", "generated", "default"]


class Parameter(_Strict):
    name: Annotated[str, Field(pattern=PARAM_PATTERN)]
    type: ParamType
    source: ParamSource
    description_nl: str
    required: bool
    default: str | int | bool | None = None
    example: str | int | bool | None = None
    dataset_role: str | None = None
    decision: str | None = None
    #: contract 1.3 §3.1 a: geheime waarde; met ``source: generated`` maakt de Hub hem (vault)
    secret: bool = False

    @property
    def hub_generated_secret(self) -> bool:
        return self.secret and self.source == "generated"


class StorageDecl(_Strict):
    name: Name
    mount: str
    kind: Literal["config", "data", "media", "scratch"]
    required_dataset_role: str | None
    description_nl: str | None = None


class Network(_Strict):
    #: De enige bron voor de poorten van het formulier (contract 1.2 §9).
    ports: list[PortBinding]
    host_network: bool
    host_network_reason: str | None = None
    #: Vervallen in contract 1.2 §9; ``false`` → ``deprecated_field``, ``true`` → ``platform_network_not_supported``.
    joins_platform_network: bool | None = None


class Device(_Strict):
    host_device: str
    container_device: str


class Privileges(_Strict):
    privileged: bool
    cap_add: list[str]
    devices: list[Device]
    #: Het maximum (contract 1.2 §9); ``custom_app.gpu`` mag nooit meer vragen.
    gpu: GpuChoice


class Monitor(_Strict):
    """Monitorplan (contract 1.3 §9): de beheerder neemt het over in Uptime Kuma ('handmatig bevestigd')."""

    pad: str
    verwachte_status: Annotated[int, Field(ge=100, le=599)]
    timeout_s: Annotated[int, Field(ge=1, le=300)]
    interval_s: Annotated[int, Field(ge=10, le=86400)]


class Healthcheck(_Strict):
    type: Literal["http", "tcp", "builtin"]
    path: str | None = None
    port: Name | None = None
    interval_s: int | None = None
    timeout_s: int | None = None
    retries: int | None = None
    start_period_s: int | None = None
    monitor: Monitor | None = None


class DbExport(_Strict):
    method: Literal["pg_dump", "sqlite_backup", "app_native", "stopped_snapshot", "none"]
    description_nl: str | None = None


class Backup(_Strict):
    db_export: DbExport
    snapshot_datasets: list[Name]
    rpo: str | ParamRef
    rto: str | ParamRef


class Dependency(_Strict):
    app_id: Annotated[str, Field(pattern=APP_ID_PATTERN)]
    kind: Literal["required", "optional"]
    min_version: str | None = None
    reason_nl: str


class Risk(_Strict):
    install: RiskClass
    update: RiskClass
    uninstall: RiskClass


class Upgrade(_Strict):
    migrations: bool
    tag_only_rollback_safe: bool
    notes_nl: str | None = None


class Compatibility(_Strict):
    truenas_min_version: str
    truenas_max_version: str | None = None
    ix_app_min_version: str | None = None


ACCEPTABLE_FINDINGS = ("backup_db_export_none", "secret_in_form", "digest_pending_release")


class AcceptedFinding(_Strict):
    code: Literal["backup_db_export_none", "secret_in_form", "digest_pending_release"]
    reden_nl: str


class PostInstallCheck(_Strict):
    id: Name
    soort: Literal["http", "handmatig"]
    beschrijving_nl: str


class Provenance(_Strict):
    source_repo: str | None = None
    source_commit: str | None = None
    upstream_repo: str | None = None
    license: str | None = None


class Manifest(_Strict):
    manifest_version: Literal["1"]
    app_id: Annotated[str, Field(pattern=APP_ID_PATTERN, max_length=40)]
    name: str
    owner_repo: str
    category: str
    description_nl: str
    version: str
    install: Install
    image: Image | None = None
    custom_app: CustomApp | None = None
    catalog: Catalog | None = None
    values: dict[str, Any] | None = None
    parameters: list[Parameter]
    storage: list[StorageDecl]
    network: Network
    privileges: Privileges
    privilege_reason: str | None = None
    healthcheck: Healthcheck
    backup: Backup
    dependencies: list[Dependency]
    sso: Literal["oidc", "native", "none"]
    risk: Risk
    upgrade: Upgrade
    decisions_required: list[str]
    docs: list[str]
    validation_status: Literal["unvalidated", "unit_tested", "sim_tested", "validated_on_nas"]
    compatibility: Compatibility | None = None
    provenance: Provenance | None = None
    accepted_findings: list[AcceptedFinding] = Field(default_factory=list)
    post_install_checks: list[PostInstallCheck] = Field(default_factory=list)

    def parameter(self, name: str) -> Parameter | None:
        return next((p for p in self.parameters if p.name == name), None)

    def storage_decl(self, name: str) -> StorageDecl | None:
        return next((s for s in self.storage if s.name == name), None)
