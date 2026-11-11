"""Getypte modellen van TrueNAS ``app.create`` voor een standaard Custom App (catalogusapp ``ix-app``) of catalogusapp.

Bron (alleen gelezen, niet gekopieerd):

* ``github.com/truenas/apps`` commit ``f142a16a1e15da5f8517658f5a9ad34400972fc4`` (2026-11-06):
  ``ix-dev/stable/ix-app`` versie **1.4.8** (``app.yaml``: ``min_scale_version 24.10.2.2``, ``lib_version 2.3.18``),
  ``questions.yaml`` (formuliervelden) en ``templates/docker-compose.yaml`` (hoe de waarden gebruikt worden);
  ``app_migrations.yaml``: de poortstructuur ``bind_mode/port_number/container_port/protocol/host_ips`` geldt sinds
  ix-app **1.2.0** (migratie ``ip_port_migration`` vanaf ≤ 1.1.15).
* ``github.com/truenas/middleware`` commit ``ece9d19057ea867ad16e94160bbf27862cc4183c`` (2026-11-06):
  ``api/v28_0_0/app.py`` en ``api/v25_10_0/app.py`` ``AppCreate`` = ``{custom_app, values, custom_compose_config,
  custom_compose_config_string, catalog_app, app_name, train, version}``; ``plugins/apps/crud.py``: ``custom_app=true``
  vereist ``custom_compose_config`` (= compose, verboden), dus het standaardformulier is ``custom_app=false`` +
  ``catalog_app="ix-app"`` + ``train="stable"`` + ``values``.

Details en veldtabel: ``docs/TRUENAS_APP_VALUES.md``. Alles hier is **NIET GEVALIDEERD op een echte NAS**.
"""

from __future__ import annotations

from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field

IX_APP_CATALOG_APP = "ix-app"
IX_APP_TRAIN = "stable"
#: Versie waarvan de waardenstructuur letterlijk is nagelopen.
IX_APP_VERIFIED_VERSIONS = ("1.4.8",)
#: Ondersteunde reeks (zelfde formulierstructuur): >= 1.2.0 en < 2.0.0.
IX_APP_MIN_VERSION = (1, 2, 0)
IX_APP_MAX_EXCLUSIVE = (2, 0, 0)
#: ``min_scale_version`` uit ix-app 1.4.8 ``app.yaml``.
IX_APP_MIN_TRUENAS = "24.10.2.2"
SOURCE_APPS_COMMIT = "f142a16a1e15da5f8517658f5a9ad34400972fc4"
SOURCE_MIDDLEWARE_COMMIT = "ece9d19057ea867ad16e94160bbf27862cc4183c"

LABEL_PREFIX = "nl.lvos04.platform"


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class IxImage(_Strict):
    repository: str
    tag: str
    pull_policy: Literal["missing", "always", "never"] = "missing"


class IxEnv(_Strict):
    name: str
    value: str


class IxDevice(_Strict):
    host_device: str
    container_device: str


class IxCapabilities(_Strict):
    add: list[str] = Field(default_factory=list)


class IxRunAs(_Strict):
    user: Annotated[int, Field(ge=1)]
    group: Annotated[int, Field(ge=1)]


class IxPort(_Strict):
    bind_mode: Literal["published", "exposed"] = "published"
    port_number: Annotated[int, Field(ge=1, le=65535)]
    container_port: Annotated[int, Field(ge=1, le=65535)]
    protocol: Literal["tcp", "udp"]
    host_ips: list[str] = Field(default_factory=list)


class IxDnsConfig(_Strict):
    nameservers: list[str] = Field(default_factory=list)
    searches: list[str] = Field(default_factory=list)
    options: list[str] = Field(default_factory=list)


class IxPortal(_Strict):
    name: str
    scheme: Literal["http", "https"]
    use_node_ip: bool = True
    host: str = ""
    port: Annotated[int, Field(ge=1, le=65535)]
    path: str


class IxHostPathConfig(_Strict):
    acl_enable: Literal[False] = False
    path: str


class IxVolumeConfig(_Strict):
    acl_enable: Literal[False] = False
    dataset_name: str


class IxTmpfsConfig(_Strict):
    size: Annotated[int, Field(ge=1)]


class IxStorage(_Strict):
    type: Literal["host_path", "ix_volume", "tmpfs"]
    read_only: bool = False
    mount_path: str
    host_path_config: IxHostPathConfig | None = None
    ix_volume_config: IxVolumeConfig | None = None
    tmpfs_config: IxTmpfsConfig | None = None


class IxLabel(_Strict):
    key: str
    value: str


class IxLimits(_Strict):
    cpus: Annotated[int, Field(ge=1)]
    memory: Annotated[int, Field(ge=64)]  # MB volgens het formulier ("Memory (in MB)")


class IxGpus(_Strict):
    use_all_gpus: bool = False
    nvidia_gpu_selection: dict[str, Any] = Field(default_factory=dict)


class IxResources(_Strict):
    enable_resource_limits: bool
    limits: IxLimits | None = None
    gpus: IxGpus = Field(default_factory=IxGpus)


class IxAppValues(_Strict):
    """Waarden van het standaard Custom App-formulier (ix-app 1.4.8 ``questions.yaml``)."""

    notes: str = ""
    image: IxImage
    hostname: str = ""
    entrypoint: list[str] = Field(default_factory=list)
    command: list[str] = Field(default_factory=list)
    TZ: str
    envs: list[IxEnv] = Field(default_factory=list)
    restart_policy: Literal["no", "unless-stopped", "on-failure", "always"]
    max_retry_count: int | None = None
    disable_builtin_healthcheck: bool = False
    tty: bool = False
    stdin: bool = False
    devices: list[IxDevice] = Field(default_factory=list)
    privileged: Literal[False] = False
    capabilities: IxCapabilities = Field(default_factory=IxCapabilities)
    run_as_custom_user: bool = False
    run_as: IxRunAs | None = None
    host_network: bool = False
    ports: list[IxPort] = Field(default_factory=list)
    networks: list[dict[str, Any]] = Field(default_factory=list)
    dns_config: IxDnsConfig = Field(default_factory=IxDnsConfig)
    portals: list[IxPortal] = Field(default_factory=list)
    storage: list[IxStorage] = Field(default_factory=list)
    labels: list[IxLabel] = Field(default_factory=list)
    resources: IxResources


class AppCreatePayload(_Strict):
    """Argument van JSON-RPC ``app.create`` (contract §8: nooit ``custom_compose_config``)."""

    app_name: Annotated[str, Field(pattern=r"^[a-z]([-a-z0-9]*[a-z0-9])?$", min_length=1, max_length=40)]
    custom_app: Literal[False] = False
    catalog_app: str
    train: str
    version: str
    values: dict[str, Any]

    def method(self) -> str:
        return "app.create"


def parse_version(text: str | None) -> tuple[int, ...] | None:
    if not text:
        return None
    parts = text.strip().split("-")[0].split(".")
    try:
        return tuple(int(p) for p in parts)
    except ValueError:
        return None
