"""Veilig inlezen van manifest-YAML.

- alleen ``yaml.SafeLoader``-typen; eigen tags geweigerd;
- ankers/aliassen geweigerd (geen "billion laughs", geen verborgen hergebruik van blokken);
- merge-sleutels (``<<``) geweigerd, ook zonder anker (verstopte velden);
- dubbele sleutels geweigerd (YAML zou stil de laatste waarde nemen);
- alleen tekst als sleutel (geen ``on:`` → ``True``, geen lijst/object als sleutel);
- datums blijven tekst (``version: 2026-11-09`` wordt geen ``date``);
- maximale grootte en maximale nestingdiepte (geen crash door recursie).
"""

from __future__ import annotations

import re
from typing import Any

import yaml

from .findings import Finding, error

MAX_MANIFEST_BYTES = 256 * 1024
#: Maximale nesting van objecten/lijsten; een geldig manifest komt niet boven ~8.
MAX_DEPTH = 32
_MERGE_TAG = "tag:yaml.org,2002:merge"


class _ManifestLoader(yaml.SafeLoader):
    """SafeLoader zonder timestamp-conversie en met controle op dubbele sleutels."""


# timestamps niet omzetten: verwijder de impliciete resolver voor tag:yaml.org,2002:timestamp
_ManifestLoader.yaml_implicit_resolvers = {
    key: [(tag, regexp) for tag, regexp in resolvers if tag != "tag:yaml.org,2002:timestamp"]
    for key, resolvers in yaml.SafeLoader.yaml_implicit_resolvers.copy().items()
}


class DuplicateKeyError(yaml.YAMLError):
    def __init__(self, key: object, line: int) -> None:
        super().__init__(f"dubbele sleutel {key!r} op regel {line}")
        self.key = key
        self.line = line


class _RefusedKeyError(yaml.YAMLError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


def _construct_mapping(loader: _ManifestLoader, node: yaml.MappingNode, deep: bool = False) -> dict[Any, Any]:
    mapping: dict[Any, Any] = {}
    for key_node, value_node in node.value:
        line = key_node.start_mark.line + 1
        if key_node.tag == _MERGE_TAG:
            raise _RefusedKeyError(
                "yaml_merge_key_forbidden",
                f"YAML-merge-sleutel '<<' op regel {line} is niet toegestaan; schrijf elk veld volledig uit.",
            )
        if not isinstance(key_node, yaml.ScalarNode):
            raise _RefusedKeyError(
                "yaml_key_not_string", f"Sleutel op regel {line} is een lijst of object; alleen tekst is toegestaan."
            )
        key = loader.construct_object(key_node, deep=deep)
        if not isinstance(key, str):
            raise _RefusedKeyError(
                "yaml_key_not_string",
                f"Sleutel {key_node.value!r} op regel {line} wordt {type(key).__name__}; alleen tekst is toegestaan "
                "(zet de sleutel tussen aanhalingstekens).",
            )
        if key in mapping:
            raise DuplicateKeyError(key, line)
        mapping[key] = loader.construct_object(value_node, deep=deep)
    return mapping


_ManifestLoader.add_constructor(yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, _construct_mapping)

_STANDARD_TAG = re.compile(r"^tag:yaml\.org,2002:(str|int|float|bool|null|map|seq)$")


def load_manifest_text(text: str | bytes) -> tuple[Any, list[Finding]]:
    """Lees YAML (JSON is ook geldige YAML). Geeft ``(data, findings)``; bij fouten is data ``None``."""
    raw = text.encode("utf-8") if isinstance(text, str) else text
    if len(raw) > MAX_MANIFEST_BYTES:
        return None, [
            error(
                "manifest_too_large",
                "",
                f"Het manifest is {len(raw)} bytes; maximaal {MAX_MANIFEST_BYTES} bytes is toegestaan.",
            )
        ]
    try:
        decoded = raw.decode("utf-8")
    except UnicodeDecodeError:
        return None, [error("yaml_invalid", "", "Het bestand is geen geldige UTF-8-tekst.")]
    try:
        depth = 0
        for event in yaml.parse(decoded, Loader=_ManifestLoader):
            if isinstance(event, (yaml.MappingStartEvent, yaml.SequenceStartEvent)):
                depth += 1
                if depth > MAX_DEPTH:
                    return None, [
                        error(
                            "yaml_too_deep",
                            "",
                            f"Het manifest is dieper genest dan {MAX_DEPTH} niveaus; dat is nooit een geldig manifest.",
                        )
                    ]
            elif isinstance(event, (yaml.MappingEndEvent, yaml.SequenceEndEvent)):
                depth -= 1
            if isinstance(event, yaml.AliasEvent) or getattr(event, "anchor", None):
                return None, [
                    error(
                        "yaml_alias_forbidden",
                        "",
                        "YAML-ankers (&) en aliassen (*) zijn niet toegestaan; schrijf elke waarde volledig uit.",
                    )
                ]
            tag = getattr(event, "tag", None)
            if tag and not _STANDARD_TAG.match(tag) and tag not in ("!", None):
                return None, [error("yaml_custom_tag_forbidden", "", f"YAML-tag {tag!r} is niet toegestaan.")]
        data = yaml.load(decoded, Loader=_ManifestLoader)  # noqa: S506 (eigen SafeLoader-subklasse)
    except DuplicateKeyError as exc:
        return None, [error("yaml_duplicate_key", "", f"De sleutel {exc.key!r} komt dubbel voor (regel {exc.line}).")]
    except _RefusedKeyError as exc:
        return None, [error(exc.code, "", exc.message)]
    except yaml.YAMLError as exc:
        mark = getattr(exc, "problem_mark", None)
        where = f" (regel {mark.line + 1}, kolom {mark.column + 1})" if mark else ""
        return None, [error("yaml_invalid", "", f"Het bestand is geen geldige YAML{where}.")]
    if not isinstance(data, dict):
        return None, [error("manifest_not_mapping", "", "Het manifest moet een object met velden zijn.")]
    return data, []
