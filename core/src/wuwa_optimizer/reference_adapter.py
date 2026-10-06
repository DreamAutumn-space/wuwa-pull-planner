"""Reference-library assumptions shared by the CLI and HTTP adapter.

The optimizer itself remains an exact evaluator of explicit account and DPS
records.  This module applies only documented reference-library coverage rules
before those inputs reach the evaluator.
"""

from __future__ import annotations

from copy import deepcopy
from typing import Any, Mapping

from .optimizer import ValidationError


COMMON_WEAPON_PREFIX = "表内常驻·"
MAX_ASSUMED_COMMON_WEAPON_MAPPINGS = 256
COMMON_WEAPON_INSTANCES_PER_MAPPING = 4
NON_GACHA_CHARACTERS = frozenset({"漂泊者·导电", "漂泊者·衍射", "漂泊者·气动"})


def _metadata(database: Mapping[str, Any]) -> Mapping[str, Any] | None:
    metadata = database.get("metadata")
    if metadata is None:
        return None
    if not isinstance(metadata, Mapping):
        raise ValidationError("database.metadata must be an object when provided")
    return metadata


def _validated_common_weapons(metadata: Mapping[str, Any]) -> list[tuple[str, str]]:
    entries = metadata.get("assumed_common_weapons")
    if entries is None:
        return []
    if not isinstance(entries, list) or len(entries) > MAX_ASSUMED_COMMON_WEAPON_MAPPINGS:
        raise ValidationError(
            "database.metadata.assumed_common_weapons must contain at most "
            f"{MAX_ASSUMED_COMMON_WEAPON_MAPPINGS} entries"
        )

    weapons: list[tuple[str, str]] = []
    seen_characters: set[str] = set()
    for index, entry in enumerate(entries):
        path = f"database.metadata.assumed_common_weapons[{index}]"
        if not isinstance(entry, Mapping):
            raise ValidationError(f"{path} must be an object")
        character = entry.get("character")
        if not isinstance(character, str) or not character.strip():
            raise ValidationError(f"{path}.character must be a non-empty string")
        character = character.strip()
        expected_weapon = f"{COMMON_WEAPON_PREFIX}{character}"
        if entry.get("weapon") != expected_weapon:
            raise ValidationError(f"{path}.weapon must equal {expected_weapon!r}")
        refinement = entry.get("refinement")
        if isinstance(refinement, bool) or refinement != 1:
            raise ValidationError(f"{path}.refinement must be 1")
        if character in seen_characters:
            raise ValidationError(f"{path}.character is duplicated")
        seen_characters.add(character)
        weapons.append((character, expected_weapon))
    return weapons


def _declared_non_gacha_characters(metadata: Mapping[str, Any]) -> frozenset[str]:
    names = metadata.get("non_gacha_characters")
    if names is None:
        return frozenset()
    if not isinstance(names, list) or any(not isinstance(name, str) for name in names):
        raise ValidationError("database.metadata.non_gacha_characters must be a list of strings")
    return frozenset(name.strip() for name in names if name.strip()) & NON_GACHA_CHARACTERS


def _filter_non_gacha_records(account: Mapping[str, Any], database: dict[str, Any], protected: frozenset[str]) -> None:
    if not protected:
        return
    character_chains: dict[str, int] = {}
    characters = account.get("characters")
    if isinstance(characters, list):
        for entry in characters:
            if not isinstance(entry, Mapping):
                continue
            character = entry.get("character")
            chain = entry.get("chain")
            if isinstance(character, str) and isinstance(chain, int) and not isinstance(chain, bool):
                character_chains[character] = chain

    records = database.get("records")
    if not isinstance(records, list):
        return
    eligible_records: list[Any] = []
    for record in records:
        if not isinstance(record, Mapping) or not isinstance(record.get("members"), list):
            eligible_records.append(record)
            continue
        for member in record["members"]:
            if not isinstance(member, Mapping) or member.get("character") not in protected:
                continue
            required_chain = member.get("chain")
            if (
                isinstance(required_chain, bool)
                or not isinstance(required_chain, int)
                or character_chains.get(member["character"], -1) < required_chain
            ):
                break
        else:
            eligible_records.append(record)
    database["records"] = eligible_records


def prepare_reference_inputs(
    account: dict[str, Any], database: dict[str, Any], *, assume_common_weapons: bool
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Copy and apply documented reference-data coverage assumptions.

    ``account`` is the normalized physical-instance model.  Common R1 weapons
    can only be added from the exact fixed-prefix metadata mapping and only for
    simplified accounts selected by ``assume_common_weapons``.  Metadata can
    restrict the known non-gacha Rover forms, but never grants a form
    or any resonance chain.
    """

    if not isinstance(account, dict) or not isinstance(database, dict):
        raise ValidationError("account and database must be objects")
    prepared_account = deepcopy(account)
    prepared_database = deepcopy(database)
    metadata = _metadata(prepared_database)
    if metadata is None:
        return prepared_account, prepared_database

    if assume_common_weapons:
        additions = _validated_common_weapons(metadata)
        weapons = prepared_account.get("weapons")
        if not isinstance(weapons, list):
            raise ValidationError("normalized account.weapons must be a list")
        used_ids = {
            entry.get("id")
            for entry in weapons
            if isinstance(entry, Mapping) and isinstance(entry.get("id"), str)
        }
        sequence = 0
        for _, weapon in additions:
            for _ in range(COMMON_WEAPON_INSTANCES_PER_MAPPING):
                while True:
                    sequence += 1
                    instance_id = f"assumed-common-{sequence}"
                    if instance_id not in used_ids:
                        used_ids.add(instance_id)
                        break
                weapons.append({"id": instance_id, "weapon": weapon, "refinement": 1})

    _filter_non_gacha_records(prepared_account, prepared_database, _declared_non_gacha_characters(metadata))
    return prepared_account, prepared_database
