"""Exact, dependency-free optimizer for configured Wuthering Waves DPS records.

The database intentionally stores *endpoint* configurations.  A record saying a
member is C2/R3 means that the optimizer may buy every prerequisite needed to
reach that state; it must not treat C1/R2 as a separately profitable endpoint.
That property is what keeps a zero-DPS intermediate upgrade from being pruned.

Character requirements define a feasible chain interval.  When several
selected records require the same character, the selected account state must
meet the highest required chain without exceeding any selected record's
``max_chain``.  A repeatable healer still represents the same owned character,
while each of its weapon slots needs a distinct physical weapon instance.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from copy import deepcopy
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from functools import lru_cache
from math import isfinite
from typing import Any, Iterable


CHARACTER_PULL_CENTS = 8_115
WEAPON_PULL_CENTS = 5_411
DIFFICULTIES = ("低", "中低", "中", "中高", "高")
DEFAULT_REPEATABLE_HEALERS = ("守岸人", "维里奈", "莫宁", "卜灵", "白芷", "穗穗")
HEALER_REPEAT_CAPACITY = 2
_DIFFICULTY_INDEX = {name: index for index, name in enumerate(DIFFICULTIES)}
_MODES = {"single", "two_teams", "four_teams", "main_c", "fixed_team", "character"}
_COST_MODES = {"pulls", "gold"}
_GOLD_COST_CENTS = 100


class ValidationError(ValueError):
    """Raised when JSON-shaped account, database, or settings input is invalid."""


class SearchLimitError(RuntimeError):
    """Raised instead of silently approximating an exact endpoint search."""


@dataclass(frozen=True)
class CharacterAsset:
    character: str
    chain: int


@dataclass(frozen=True)
class WeaponInstance:
    id: str
    weapon: str
    refinement: int


@dataclass(frozen=True)
class Account:
    characters: tuple[CharacterAsset, ...]
    weapons: tuple[WeaponInstance, ...]


@dataclass(frozen=True)
class MemberRequirement:
    character: str
    chain: int
    weapon: str
    refinement: int
    max_chain: int = 6


@dataclass(frozen=True)
class Rotation:
    id: str
    difficulty: str
    dps: Decimal


@dataclass(frozen=True)
class DPSRecord:
    id: str
    main_c: str
    members: tuple[MemberRequirement, ...]
    rotations: tuple[Rotation, ...]


@dataclass(frozen=True)
class UpgradeAction:
    kind: str
    cost_cents: int
    character: str | None = None
    weapon: str | None = None
    instance_id: str | None = None
    from_level: int | None = None
    to_level: int | None = None


@dataclass(frozen=True)
class OptimizerSettings:
    mode: str
    budget_cents: int
    allowed_difficulties: frozenset[str]
    repeatable_healers: frozenset[str]
    healer_capacity: int = HEALER_REPEAT_CAPACITY
    target_main_c: str | None = None
    target_team: frozenset[str] | None = None
    search_limit: int = 100_000
    cost_mode: str = "pulls"
    target_character: str | None = None


@dataclass(frozen=True)
class _Endpoint:
    record: dict[str, Any]
    rotation: dict[str, Any]
    dps: Decimal
    source_index: int


def _fail(message: str) -> None:
    raise ValidationError(message)


def _mapping(value: Any, path: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        _fail(f"{path} must be an object")
    return value


def _list(value: Any, path: str) -> list[Any]:
    if not isinstance(value, list):
        _fail(f"{path} must be a list")
    return value


def _text(value: Any, path: str) -> str:
    if not isinstance(value, str) or not value.strip():
        _fail(f"{path} must be a non-empty string")
    return value.strip()


def _integer(value: Any, path: str, minimum: int, maximum: int | None = None) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        _fail(f"{path} must be an integer")
    if value < minimum or (maximum is not None and value > maximum):
        if maximum is None:
            _fail(f"{path} must be at least {minimum}")
        _fail(f"{path} must be between {minimum} and {maximum}")
    return value


def _finite_number(value: Any, path: str, *, positive: bool = False) -> Decimal:
    if isinstance(value, bool) or not isinstance(value, (int, float, Decimal)):
        _fail(f"{path} must be a finite number")
    if isinstance(value, float) and not isfinite(value):
        _fail(f"{path} must be a finite number")
    try:
        decimal = Decimal(str(value))
    except (InvalidOperation, ValueError):
        _fail(f"{path} must be a finite number")
    if not decimal.is_finite() or (positive and decimal <= 0):
        qualifier = "a positive finite number" if positive else "a finite number"
        _fail(f"{path} must be {qualifier}")
    return decimal


def _json_number(value: Decimal) -> int | float:
    """Return a JSON-friendly number without leaking Decimal into FastAPI output."""
    if value == value.to_integral_value():
        return int(value)
    return float(value)


def _cost_from_cents(cents: int) -> float:
    return round(cents / 100, 2)


def _action_cost_cents(cost_mode: str, kind: str) -> int:
    """Return the internal hundredths-unit cost for one upgrade action.

    Pull mode retains the independently modelled character and weapon
    expectations.  Gold mode deliberately makes every action one gold, while
    keeping the existing integer-cost search representation and comparisons.
    """
    if cost_mode == "gold":
        return _GOLD_COST_CENTS
    return CHARACTER_PULL_CENTS if kind == "character" else WEAPON_PULL_CENTS


def validate_account(account: dict[str, Any]) -> dict[str, Any]:
    """Validate and normalize the public account JSON contract.

    One character entry represents the account's one character.  Weapons are
    deliberately instance-based, so same-name weapons may have different IDs
    and refinement levels.
    """
    raw = _mapping(account, "account")
    characters_raw = _list(raw.get("characters"), "account.characters")
    weapons_raw = _list(raw.get("weapons"), "account.weapons")

    characters: list[dict[str, Any]] = []
    character_names: set[str] = set()
    for index, item in enumerate(characters_raw):
        entry = _mapping(item, f"account.characters[{index}]")
        character = _text(entry.get("character"), f"account.characters[{index}].character")
        if character in character_names:
            _fail(f"account.characters has duplicate character {character!r}")
        character_names.add(character)
        characters.append(
            {
                "character": character,
                "chain": _integer(entry.get("chain"), f"account.characters[{index}].chain", 0, 6),
            }
        )

    weapons: list[dict[str, Any]] = []
    weapon_ids: set[str] = set()
    for index, item in enumerate(weapons_raw):
        entry = _mapping(item, f"account.weapons[{index}]")
        instance_id = _text(entry.get("id"), f"account.weapons[{index}].id")
        if instance_id in weapon_ids:
            _fail(f"account.weapons has duplicate id {instance_id!r}")
        weapon_ids.add(instance_id)
        weapons.append(
            {
                "id": instance_id,
                "weapon": _text(entry.get("weapon"), f"account.weapons[{index}].weapon"),
                "refinement": _integer(
                    entry.get("refinement"), f"account.weapons[{index}].refinement", 1, 5
                ),
            }
        )
    return {"characters": characters, "weapons": weapons}


def account_from_json(account: dict[str, Any]) -> Account:
    """Convert validated account JSON into the public immutable domain model."""
    normalized = validate_account(account)
    return Account(
        characters=tuple(CharacterAsset(**entry) for entry in normalized["characters"]),
        weapons=tuple(WeaponInstance(**entry) for entry in normalized["weapons"]),
    )


def validate_database(database: dict[str, Any]) -> dict[str, Any]:
    """Validate and normalize endpoint DPS records supplied by an administrator."""
    raw = _mapping(database, "database")
    version = _text(raw.get("version"), "database.version")
    records_raw = _list(raw.get("records"), "database.records")
    records: list[dict[str, Any]] = []
    record_ids: set[str] = set()

    for record_index, item in enumerate(records_raw):
        path = f"database.records[{record_index}]"
        entry = _mapping(item, path)
        record_id = _text(entry.get("id"), f"{path}.id")
        if record_id in record_ids:
            _fail(f"database.records has duplicate id {record_id!r}")
        record_ids.add(record_id)
        main_c = _text(entry.get("main_c"), f"{path}.main_c")

        members_raw = _list(entry.get("members"), f"{path}.members")
        if len(members_raw) != 3:
            _fail(f"{path}.members must contain exactly three members")
        members: list[dict[str, Any]] = []
        member_names: set[str] = set()
        for member_index, member_item in enumerate(members_raw):
            member_path = f"{path}.members[{member_index}]"
            member = _mapping(member_item, member_path)
            character = _text(member.get("character"), f"{member_path}.character")
            if character in member_names:
                _fail(f"{path}.members has duplicate character {character!r}")
            member_names.add(character)
            chain = _integer(member.get("chain"), f"{member_path}.chain", 0, 6)
            members.append(
                {
                    "character": character,
                    "chain": chain,
                    "max_chain": _integer(member.get("max_chain", 6), f"{member_path}.max_chain", chain, 6),
                    "weapon": _text(member.get("weapon"), f"{member_path}.weapon"),
                    "refinement": _integer(member.get("refinement"), f"{member_path}.refinement", 1, 5),
                }
            )
        if main_c not in member_names:
            _fail(f"{path}.main_c must occur in {path}.members")

        rotations_raw = _list(entry.get("rotations"), f"{path}.rotations")
        if not rotations_raw:
            _fail(f"{path}.rotations must not be empty")
        rotations: list[dict[str, Any]] = []
        rotation_ids: set[str] = set()
        for rotation_index, rotation_item in enumerate(rotations_raw):
            rotation_path = f"{path}.rotations[{rotation_index}]"
            rotation = _mapping(rotation_item, rotation_path)
            rotation_id = _text(rotation.get("id"), f"{rotation_path}.id")
            if rotation_id in rotation_ids:
                _fail(f"{path}.rotations has duplicate id {rotation_id!r}")
            rotation_ids.add(rotation_id)
            difficulty = _text(rotation.get("difficulty"), f"{rotation_path}.difficulty")
            if difficulty not in _DIFFICULTY_INDEX:
                _fail(f"{rotation_path}.difficulty must be one of {', '.join(DIFFICULTIES)}")
            dps = _finite_number(rotation.get("dps"), f"{rotation_path}.dps", positive=True)
            rotations.append({"id": rotation_id, "difficulty": difficulty, "dps": _json_number(dps)})

        records.append({"id": record_id, "main_c": main_c, "members": members, "rotations": rotations})
    return {"version": version, "records": records}


def records_from_json(database: dict[str, Any]) -> tuple[DPSRecord, ...]:
    """Convert validated database JSON into immutable endpoint domain records."""
    normalized = validate_database(database)
    return tuple(
        DPSRecord(
            id=record["id"],
            main_c=record["main_c"],
            members=tuple(MemberRequirement(**member) for member in record["members"]),
            rotations=tuple(
                Rotation(
                    id=rotation["id"],
                    difficulty=rotation["difficulty"],
                    dps=Decimal(str(rotation["dps"])),
                )
                for rotation in record["rotations"]
            ),
        )
        for record in normalized["records"]
    )


def _validate_settings(settings: dict[str, Any]) -> dict[str, Any]:
    raw = _mapping(settings, "settings")
    mode = _text(raw.get("mode"), "settings.mode")
    if mode not in _MODES:
        _fail("settings.mode must be one of single, two_teams, four_teams, main_c, fixed_team, character")
    cost_mode = _text(raw.get("cost_mode", "pulls"), "settings.cost_mode")
    if cost_mode not in _COST_MODES:
        _fail("settings.cost_mode must be one of pulls, gold")
    budget = _finite_number(raw.get("budget"), "settings.budget")
    if budget < 0:
        _fail("settings.budget must be non-negative")
    if cost_mode == "gold":
        if budget != budget.to_integral_value():
            _fail("settings.budget must be a non-negative integer in gold mode")
        budget_cents_decimal = budget * _GOLD_COST_CENTS
    else:
        budget_cents_decimal = budget * 100
        if budget_cents_decimal != budget_cents_decimal.to_integral_value():
            _fail("settings.budget must have no more than two decimal places")

    max_difficulty = _text(raw.get("max_difficulty", "中"), "settings.max_difficulty")
    if max_difficulty not in _DIFFICULTY_INDEX:
        _fail(f"settings.max_difficulty must be one of {', '.join(DIFFICULTIES)}")
    allowed_raw = raw.get("allowed_difficulties")
    if allowed_raw is None:
        allowed = list(DIFFICULTIES[: _DIFFICULTY_INDEX[max_difficulty] + 1])
    else:
        allowed_values = _list(allowed_raw, "settings.allowed_difficulties")
        if not allowed_values:
            _fail("settings.allowed_difficulties must not be empty")
        allowed = []
        for index, difficulty_raw in enumerate(allowed_values):
            difficulty = _text(difficulty_raw, f"settings.allowed_difficulties[{index}]")
            if difficulty not in _DIFFICULTY_INDEX:
                _fail(f"settings.allowed_difficulties[{index}] is not a valid difficulty")
            if difficulty in allowed:
                _fail(f"settings.allowed_difficulties has duplicate difficulty {difficulty!r}")
            allowed.append(difficulty)

    # An omitted whitelist opts into the product defaults.  An explicit empty
    # list is intentionally preserved: it means the player has disabled every
    # cross-team healer repeat for this calculation.
    repeatable_values = (
        _list(raw["repeatable_healers"], "settings.repeatable_healers")
        if "repeatable_healers" in raw
        else list(DEFAULT_REPEATABLE_HEALERS)
    )
    repeatable: list[str] = []
    for index, character_raw in enumerate(repeatable_values):
        character = _text(character_raw, f"settings.repeatable_healers[{index}]")
        if character in repeatable:
            _fail(f"settings.repeatable_healers has duplicate character {character!r}")
        repeatable.append(character)

    # This field used to be configurable.  Keep accepting old positive integer
    # requests so saved payloads remain valid, but normalize every one to the
    # fixed two-team rule.  _integer deliberately retains the JSON type check.
    if "healer_capacity" in raw:
        _integer(raw["healer_capacity"], "settings.healer_capacity", 1)
    healer_capacity = HEALER_REPEAT_CAPACITY
    search_limit = _integer(raw.get("search_limit", 100_000), "settings.search_limit", 1)

    target_main_c: str | None = None
    if mode == "main_c":
        target_main_c = _text(raw.get("target_main_c"), "settings.target_main_c")

    target_character: str | None = None
    if mode == "character":
        target_character = _text(raw.get("target_character"), "settings.target_character")

    target_team: list[str] | None = None
    if mode == "fixed_team":
        target_values = _list(raw.get("target_team"), "settings.target_team")
        if len(target_values) != 3:
            _fail("settings.target_team must contain exactly three characters")
        target_team = []
        for index, character_raw in enumerate(target_values):
            character = _text(character_raw, f"settings.target_team[{index}]")
            if character in target_team:
                _fail(f"settings.target_team has duplicate character {character!r}")
            target_team.append(character)

    return {
        "mode": mode,
        "cost_mode": cost_mode,
        "budget_cents": int(budget_cents_decimal),
        "allowed_difficulties": frozenset(allowed),
        "repeatable_healers": frozenset(repeatable),
        "healer_capacity": healer_capacity,
        "target_main_c": target_main_c,
        "target_team": frozenset(target_team) if target_team is not None else None,
        "target_character": target_character,
        "search_limit": search_limit,
    }


def settings_from_json(settings: dict[str, Any]) -> OptimizerSettings:
    """Convert public settings JSON to the immutable settings domain model."""
    normalized = _validate_settings(settings)
    return OptimizerSettings(
        mode=normalized["mode"],
        cost_mode=normalized["cost_mode"],
        budget_cents=normalized["budget_cents"],
        allowed_difficulties=normalized["allowed_difficulties"],
        repeatable_healers=normalized["repeatable_healers"],
        healer_capacity=normalized["healer_capacity"],
        target_main_c=normalized["target_main_c"],
        target_team=normalized["target_team"],
        target_character=normalized["target_character"],
        search_limit=normalized["search_limit"],
    )


def _team_count(mode: str) -> int:
    return {"single": 1, "main_c": 1, "fixed_team": 1, "character": 1, "two_teams": 2, "four_teams": 4}[mode]


def _representative_endpoints(database: dict[str, Any], settings: dict[str, Any]) -> list[_Endpoint]:
    endpoints: list[_Endpoint] = []
    target_team = settings["target_team"]
    for index, record in enumerate(database["records"]):
        if settings["mode"] == "main_c" and record["main_c"] != settings["target_main_c"]:
            continue
        if settings["mode"] == "character" and settings["target_character"] not in {
            member["character"] for member in record["members"]
        }:
            continue
        if settings["mode"] == "fixed_team":
            member_names = frozenset(member["character"] for member in record["members"])
            if member_names != target_team:
                continue
        allowed_rotations = [
            rotation for rotation in record["rotations"] if rotation["difficulty"] in settings["allowed_difficulties"]
        ]
        if not allowed_rotations:
            continue
        # Keep source order as the deterministic tiebreak after the highest DPS.
        rotation = max(allowed_rotations, key=lambda candidate: Decimal(str(candidate["dps"])))
        endpoints.append(
            _Endpoint(record=record, rotation=rotation, dps=Decimal(str(rotation["dps"])), source_index=index)
        )
    return endpoints


def _new_instance_id(used_ids: set[str], sequence: int) -> tuple[str, int]:
    while True:
        sequence += 1
        instance_id = f"planned-{sequence}"
        if instance_id not in used_ids:
            used_ids.add(instance_id)
            return instance_id, sequence


def _assign_weapons(
    slots: list[dict[str, Any]],
    account_weapons: list[dict[str, Any]],
    *,
    action_cost_cents: int = WEAPON_PULL_CENTS,
) -> tuple[dict[int, dict[str, Any]], list[dict[str, Any]], int]:
    """Find an exact minimum-cost physical-weapon assignment.

    Names do not substitute for one another, so each weapon name is an
    independent matching problem.  Starting with a new weapon for every slot
    costs ``sum(required refinement)`` upgrade actions.  An owned weapon of rank ``r``
    saves ``min(r, required)`` pulls.  Thus the optimal matching takes the
    highest owned ranks and highest requested ranks, pairing both in ascending
    order (the standard exchange argument for ``min(r, required)``).  This is
    an exact minimum-cost matching, not a greedy endpoint pruning rule.
    """
    slots_by_weapon: dict[str, list[dict[str, Any]]] = defaultdict(list)
    owned_by_weapon: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for slot in slots:
        slots_by_weapon[slot["weapon"]].append(slot)
    for instance in account_weapons:
        owned_by_weapon[instance["weapon"]].append(instance)

    assignments: dict[int, dict[str, Any]] = {}
    actions: list[dict[str, Any]] = []
    cents = 0
    used_ids = {weapon["id"] for weapon in account_weapons}
    sequence = 0

    for weapon_name in sorted(slots_by_weapon):
        group_slots = slots_by_weapon[weapon_name]
        owned = owned_by_weapon.get(weapon_name, [])
        matched_count = min(len(group_slots), len(owned))
        # The highest-ranked physical instances dominate lower-ranked ones.
        selected_owned = sorted(
            sorted(owned, key=lambda instance: (instance["refinement"], instance["id"]), reverse=True)[:matched_count],
            key=lambda instance: (instance["refinement"], instance["id"]),
        )
        selected_slots = sorted(
            sorted(group_slots, key=lambda slot: (slot["refinement"], slot["slot_index"]), reverse=True)[:matched_count],
            key=lambda slot: (slot["refinement"], slot["slot_index"]),
        )
        selected_slot_ids = {slot["slot_index"] for slot in selected_slots}

        for slot, instance in zip(selected_slots, selected_owned):
            target = slot["refinement"]
            current = instance["refinement"]
            final = max(target, current)
            assignments[slot["slot_index"]] = {
                "instance_id": instance["id"],
                "weapon": weapon_name,
                "refinement": final,
            }
            for next_rank in range(current + 1, target + 1):
                actions.append(
                    {
                        "kind": "weapon_refinement",
                        "weapon": weapon_name,
                        "instance_id": instance["id"],
                        "from_refinement": next_rank - 1,
                        "to_refinement": next_rank,
                        "cost": _cost_from_cents(action_cost_cents),
                    }
                )
                cents += action_cost_cents

        for slot in sorted(group_slots, key=lambda slot: slot["slot_index"]):
            if slot["slot_index"] in selected_slot_ids:
                continue
            instance_id, sequence = _new_instance_id(used_ids, sequence)
            target = slot["refinement"]
            assignments[slot["slot_index"]] = {
                "instance_id": instance_id,
                "weapon": weapon_name,
                "refinement": target,
            }
            for next_rank in range(1, target + 1):
                actions.append(
                    {
                        "kind": "weapon_acquisition" if next_rank == 1 else "weapon_refinement",
                        "weapon": weapon_name,
                        "instance_id": instance_id,
                        "from_refinement": None if next_rank == 1 else next_rank - 1,
                        "to_refinement": next_rank,
                        "cost": _cost_from_cents(action_cost_cents),
                    }
                )
                cents += action_cost_cents
    return assignments, actions, cents


def _build_plan(
    endpoints: tuple[_Endpoint, ...], account: dict[str, Any], settings: dict[str, Any]
) -> dict[str, Any] | None:
    character_action_cost_cents = _action_cost_cents(settings["cost_mode"], "character")
    weapon_action_cost_cents = _action_cost_cents(settings["cost_mode"], "weapon")
    appearances: Counter[str] = Counter()
    required_chains: dict[str, int] = {}
    maximum_chains: dict[str, int] = {}
    slots: list[dict[str, Any]] = []
    for team_index, endpoint in enumerate(endpoints):
        for member_index, member in enumerate(endpoint.record["members"]):
            character = member["character"]
            appearances[character] += 1
            required_chains[character] = max(required_chains.get(character, 0), member["chain"])
            maximum_chains[character] = min(maximum_chains.get(character, 6), member["max_chain"])
            slots.append(
                {
                    "slot_index": len(slots),
                    "team_index": team_index,
                    "member_index": member_index,
                    "character": character,
                    "weapon": member["weapon"],
                    "refinement": member["refinement"],
                }
            )
    for character, uses in appearances.items():
        capacity = settings["healer_capacity"] if character in settings["repeatable_healers"] else 1
        if uses > capacity:
            return None

    owned_chains = {character["character"]: character["chain"] for character in account["characters"]}
    character_actions: list[dict[str, Any]] = []
    character_cents = 0
    for character in sorted(required_chains):
        required = required_chains[character]
        current = owned_chains.get(character, -1)
        final_chain = max(current, required)
        if final_chain > maximum_chains[character]:
            # One account has one chain state.  A selected record cannot be
            # used at a lower chain to accommodate another selected record or
            # an already-owned higher-chain character.
            return None
        for next_chain in range(current + 1, final_chain + 1):
            character_actions.append(
                {
                    "kind": "character_acquisition" if next_chain == 0 else "character_chain",
                    "character": character,
                    "from_chain": None if next_chain == 0 else next_chain - 1,
                    "to_chain": next_chain,
                    "cost": _cost_from_cents(character_action_cost_cents),
                }
            )
            character_cents += character_action_cost_cents

    assignments, weapon_actions, weapon_cents = _assign_weapons(
        slots,
        account["weapons"],
        action_cost_cents=weapon_action_cost_cents,
    )
    teams: list[dict[str, Any]] = []
    for team_index, endpoint in enumerate(endpoints):
        team_slots = [slot for slot in slots if slot["team_index"] == team_index]
        teams.append(
            {
                "record_id": endpoint.record["id"],
                "main_c": endpoint.record["main_c"],
                "members": [dict(member) for member in endpoint.record["members"]],
                "rotation_id": endpoint.rotation["id"],
                "difficulty": endpoint.rotation["difficulty"],
                "dps": _json_number(endpoint.dps),
                "weapon_assignment": [
                    {
                        "character": slot["character"],
                        "instance_id": assignments[slot["slot_index"]]["instance_id"],
                        "weapon": assignments[slot["slot_index"]]["weapon"],
                        "refinement": assignments[slot["slot_index"]]["refinement"],
                    }
                    for slot in team_slots
                ],
            }
        )
    total_dps = sum((endpoint.dps for endpoint in endpoints), Decimal("0"))
    return {
        "feasible": True,
        "total_dps": _json_number(total_dps),
        "teams": teams,
        "cost": _cost_from_cents(character_cents + weapon_cents),
        "actions": character_actions + weapon_actions,
        "gold_count": len(character_actions) + len(weapon_actions),
        "_dps": total_dps,
        "_cost_cents": character_cents + weapon_cents,
        "_key": tuple(endpoint.source_index for endpoint in endpoints),
    }


def _empty_plan(cost_mode: str = "pulls") -> dict[str, Any]:
    return {
        "feasible": False,
        "total_dps": 0,
        "teams": [],
        "cost": 0,
        "actions": [],
        "gold_count": 0,
        "gain": 0,
        "gain_percent": 0,
        "roi_per_100_pulls": 0 if cost_mode == "pulls" else None,
        "roi_per_gold": 0 if cost_mode == "gold" else None,
    }


def _with_gain(plan: dict[str, Any], baseline: Decimal, cost_mode: str = "pulls") -> dict[str, Any]:
    result = {key: value for key, value in plan.items() if not key.startswith("_")}
    gain = plan["_dps"] - baseline
    result["gain"] = _json_number(gain)
    result["gain_percent"] = None if baseline == 0 else float((gain * 100 / baseline).quantize(Decimal("0.0001")))
    if cost_mode == "gold":
        result["roi_per_100_pulls"] = None
        result["roi_per_gold"] = (
            0
            if plan["_cost_cents"] == 0
            else float((gain * _GOLD_COST_CENTS / Decimal(plan["_cost_cents"])).quantize(Decimal("0.0001")))
        )
    else:
        result["roi_per_100_pulls"] = (
            0
            if plan["_cost_cents"] == 0
            else float((gain * 10_000 / Decimal(plan["_cost_cents"])).quantize(Decimal("0.0001")))
        )
        result["roi_per_gold"] = None
    return result


def _better_current(candidate: dict[str, Any], existing: dict[str, Any] | None) -> bool:
    if existing is None:
        return True
    return (candidate["_dps"], tuple(-part for part in candidate["_key"])) > (
        existing["_dps"],
        tuple(-part for part in existing["_key"]),
    )


def _better_best(candidate: dict[str, Any], existing: dict[str, Any] | None) -> bool:
    if existing is None:
        return True
    return (
        candidate["_dps"] > existing["_dps"]
        or (
            candidate["_dps"] == existing["_dps"]
            and (candidate["_cost_cents"], candidate["_key"]) < (existing["_cost_cents"], existing["_key"])
        )
    )


def _pareto(plans: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    # Sort deterministicly by cost then descending DPS.  Keeping a plan only
    # when it raises DPS leaves precisely the non-dominated cost/DPS frontier.
    sorted_plans = sorted(plans, key=lambda plan: (plan["_cost_cents"], -plan["_dps"], plan["_key"]))
    frontier: list[dict[str, Any]] = []
    best_dps: Decimal | None = None
    for plan in sorted_plans:
        if best_dps is not None and plan["_dps"] <= best_dps:
            continue
        frontier.append(plan)
        best_dps = plan["_dps"]
    return frontier


def _pareto_dominates(left: dict[str, Any], right: dict[str, Any]) -> bool:
    """Whether ``left`` is the representative the Pareto output would keep.

    The public frontier keeps one deterministic representative for equal cost
    and DPS points: the lower source-key wins.  Keeping that rule here lets us
    retain only the frontier during search rather than every complete plan.
    """
    if left["_cost_cents"] > right["_cost_cents"] or left["_dps"] < right["_dps"]:
        return False
    if left["_cost_cents"] < right["_cost_cents"] or left["_dps"] > right["_dps"]:
        return True
    return left["_key"] <= right["_key"]


def _insert_pareto(frontier: list[dict[str, Any]], plan: dict[str, Any]) -> None:
    """Insert one budget-valid plan into an in-memory non-dominated frontier."""
    if any(_pareto_dominates(existing, plan) for existing in frontier):
        return
    frontier[:] = [existing for existing in frontier if not _pareto_dominates(plan, existing)]
    frontier.append(plan)


def _strictly_dominates_upper_bound(
    frontier: Iterable[dict[str, Any]], lower_cost_cents: int, upper_dps: Decimal
) -> bool:
    """Return whether every completion is dominated by an already seen plan.

    Any extension of a partial team set only adds character/weapon constraints,
    so its exact minimum cost cannot be below ``lower_cost_cents``.  The
    caller's DPS bound likewise covers every extension.  Equal cost/DPS needs
    its source-key tiebreak resolved at a leaf, hence this deliberately uses
    only strict dominance cases.
    """
    for plan in frontier:
        if plan["_cost_cents"] < lower_cost_cents and plan["_dps"] >= upper_dps:
            return True
        if plan["_cost_cents"] <= lower_cost_cents and plan["_dps"] > upper_dps:
            return True
    return False


class _SearchCounter:
    """Enforce a hard limit on states actually materialized by exact search."""

    def __init__(self, limit: int):
        self.limit = limit
        self.visited = 0

    def visit(self) -> None:
        self.visited += 1
        if self.visited > self.limit:
            raise SearchLimitError(
                f"Exact search visited {self.visited} states, exceeding search_limit={self.limit}"
            )


def _roster_requirements(endpoint: _Endpoint, settings: dict[str, Any]) -> tuple[tuple[str, int, int, int], ...]:
    return tuple(
        (member["character"], member["chain"], member["max_chain"],
         settings["healer_capacity"] if member["character"] in settings["repeatable_healers"] else 1)
        for member in endpoint.record["members"]
    )


def _extend_roster(
    roster: dict[str, tuple[int, int, int]], requirements: tuple[tuple[str, int, int, int], ...]
) -> dict[str, tuple[int, int, int]] | None:
    """Reject character conflicts before materializing weapon/cost plans."""
    updates = {}
    for name, minimum, maximum, capacity in requirements:
        previous = roster.get(name)
        uses = 1
        if previous is not None:
            uses = previous[0] + 1
            minimum = max(minimum, previous[1])
            maximum = min(maximum, previous[2])
        if uses > capacity or minimum > maximum:
            return None
        updates[name] = (uses, minimum, maximum)
    return {**roster, **updates}


def _inventory_requirements(account: dict[str, Any]) -> tuple[dict[str, int], dict[str, tuple[int, ...]]]:
    weapons: dict[str, list[int]] = defaultdict(list)
    for instance in account["weapons"]:
        weapons[instance["weapon"]].append(instance["refinement"])
    return (
        {item["character"]: item["chain"] for item in account["characters"]},
        {name: tuple(sorted(ranks, reverse=True)) for name, ranks in weapons.items()},
    )


def _plan_summary(
    endpoints: tuple[_Endpoint, ...], roster: dict[str, tuple[int, int, int]],
    inventory: tuple[dict[str, int], dict[str, tuple[int, ...]]], settings: dict[str, Any],
) -> dict[str, Any] | None:
    """Exact cost without constructing actions or physical assignments.

    For each weapon name, the same matching rule as _assign_weapons saves
    min(owned rank, required rank) copies by pairing the highest ranks. Only
    returned plans need the full physical-instance assignment and action list.
    """
    owned_chains, owned_weapons = inventory
    character_count = 0
    for name, (_, minimum, maximum) in roster.items():
        current = owned_chains.get(name, -1)
        if max(current, minimum) > maximum:
            return None
        character_count += max(0, minimum - current)
    required_weapons: dict[str, list[int]] = defaultdict(list)
    for endpoint in endpoints:
        for member in endpoint.record["members"]:
            required_weapons[member["weapon"]].append(member["refinement"])
    weapon_count = 0
    for name, ranks in required_weapons.items():
        required = sorted(ranks, reverse=True)
        saved = sum(min(need, owned) for need, owned in zip(required, owned_weapons.get(name, ())))
        weapon_count += sum(required) - saved
    cost = (
        character_count * _action_cost_cents(settings["cost_mode"], "character")
        + weapon_count * _action_cost_cents(settings["cost_mode"], "weapon")
    )
    dps = sum((endpoint.dps for endpoint in endpoints), Decimal("0"))
    return {
        "_dps": dps,
        "_cost_cents": cost,
        "_key": tuple(endpoint.source_index for endpoint in endpoints),
        "gold_count": character_count + weapon_count,
    }


def _best_owned_plan(
    endpoints: list[_Endpoint], account: dict[str, Any], settings: dict[str, Any], counter: _SearchCounter
) -> dict[str, Any] | None:
    """Repack the requested full team set using only the assets now owned."""
    inventory = _inventory_requirements(account)
    available = []
    for endpoint in endpoints:
        counter.visit()
        roster = _extend_roster({}, _roster_requirements(endpoint, settings))
        plan = _plan_summary((endpoint,), roster, inventory, settings)
        if plan is not None and plan["_cost_cents"] == 0:
            available.append((endpoint, plan))
    available.sort(key=lambda item: (-item[0].dps, item[0].source_index))
    rosters = [_roster_requirements(endpoint, settings) for endpoint, _ in available]
    count = _team_count(settings["mode"])
    best = None

    def search(
        selected: tuple[_Endpoint, ...], start: int, plan: dict[str, Any], roster: dict[str, tuple[int, int, int]]
    ) -> None:
        nonlocal best
        remaining = count - len(selected)
        if not remaining:
            if _better_current(plan, best):
                best = plan
            return
        for index in range(start, len(available)):
            # Later endpoints have no higher DPS. This bound is safe even if
            # the same endpoint cannot actually fill all remaining slots.
            if best is not None and plan["_dps"] + available[index][0].dps * remaining < best["_dps"]:
                break
            child_roster = _extend_roster(roster, rosters[index])
            if child_roster is None:
                continue
            counter.visit()
            child = tuple(sorted((*selected, available[index][0]), key=lambda item: item.source_index))
            child_plan = _plan_summary(child, child_roster, inventory, settings)
            if child_plan is not None and child_plan["_cost_cents"] == 0:
                search(child, index, child_plan, child_roster)

    for index, (endpoint, plan) in enumerate(available):
        search((endpoint,), index, plan, _extend_roster({}, rosters[index]))
    if best is None:
        return None
    by_index = {endpoint.source_index: endpoint for endpoint in endpoints}
    return _build_plan(tuple(by_index[index] for index in best["_key"]), account, settings)


def _optimal_upgrade_path(
    account: dict[str, Any], endpoints: list[_Endpoint], settings: dict[str, Any],
    best: dict[str, Any], current: dict[str, Any] | None, counter: _SearchCounter,
) -> list[dict[str, Any]]:
    """Maximize the prefix DPS vector among prerequisite-valid final purchases.

    Final purchases are fixed by the exact budget winner. Equal immediate DPS
    is resolved by looking ahead, so a zero-gain prerequisite can precede an
    unrelated upgrade. Every prefix is evaluated with a fresh full-team packing.
    """
    groups: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for action in best["actions"]:
        key = ("character", action["character"]) if "character" in action else ("weapon", action["instance_id"])
        groups.setdefault(key, []).append(action)
    purchases = list(groups.values())
    if not purchases:
        return []
    initial = (0,) * len(purchases)
    final = tuple(len(group) for group in purchases)

    @lru_cache(maxsize=None)
    def evaluate(state: tuple[int, ...]) -> dict[str, Any] | None:
        if state == initial:
            return current
        inventory = deepcopy(account)
        characters = {item["character"]: item for item in inventory["characters"]}
        weapons = {item["id"]: item for item in inventory["weapons"]}
        for group, progress in zip(purchases, state):
            if not progress:
                continue
            action = group[progress - 1]
            if "character" in action:
                name = action["character"]
                if name not in characters:
                    characters[name] = {"character": name, "chain": action["to_chain"]}
                    inventory["characters"].append(characters[name])
                characters[name]["chain"] = action["to_chain"]
            else:
                instance_id = action["instance_id"]
                if instance_id not in weapons:
                    weapons[instance_id] = {"id": instance_id, "weapon": action["weapon"], "refinement": action["to_refinement"]}
                    inventory["weapons"].append(weapons[instance_id])
                weapons[instance_id]["refinement"] = action["to_refinement"]
        return _best_owned_plan(endpoints, inventory, settings, counter)

    @lru_cache(maxsize=None)
    def order(state: tuple[int, ...]) -> tuple[tuple[Decimal, ...], tuple[int, ...]]:
        if state == final:
            return (), ()
        candidates = []
        for index, progress in enumerate(state):
            if progress == final[index]:
                continue
            child = (*state[:index], progress + 1, *state[index + 1:])
            plan = evaluate(child)
            score = plan["_dps"] if plan is not None else Decimal("-1")
            candidates.append((score, index, child))
        highest = max(item[0] for item in candidates)
        winner = None
        for score, index, child in candidates:
            if score != highest:
                continue
            suffix_scores, suffix_order = order(child)
            candidate = ((score, *suffix_scores), (index, *suffix_order))
            if winner is None or candidate[0] > winner[0]:
                winner = candidate
        assert winner is not None
        return winner

    _, sequence = order(initial)
    state = initial
    previous = current
    cost_cents = 0
    steps = []
    for number, index in enumerate(sequence, 1):
        action = purchases[index][state[index]]
        state = (*state[:index], state[index] + 1, *state[index + 1:])
        plan = evaluate(state)
        cost_cents += _action_cost_cents(settings["cost_mode"], "character" if "character" in action else "weapon")
        gain = plan["_dps"] - previous["_dps"] if plan is not None and previous is not None else None
        steps.append({
            "gold": number,
            "action": dict(action),
            "cumulative_cost": _cost_from_cents(cost_cents),
            "feasible": plan is not None,
            "total_dps": plan["total_dps"] if plan is not None else None,
            "teams": plan["teams"] if plan is not None else [],
            "gain": _json_number(gain) if gain is not None else None,
            "gain_percent": float((gain * 100 / previous["_dps"]).quantize(Decimal("0.0001")))
                if gain is not None and previous["_dps"] > 0 else None,
        })
        previous = plan
    return steps


def optimize(account: dict[str, Any], database: dict[str, Any], settings: dict[str, Any]) -> dict[str, Any]:
    """Return exact current, budgeted-best, and Pareto endpoint plans.

    The function is deliberately stateless: anonymous browser sessions can send
    their account JSON in a request or retain it client-side.  If the endpoint
    search would exceed ``settings.search_limit``, a :class:`SearchLimitError`
    is raised rather than returning a heuristic result.
    """
    normalized_account = validate_account(account)
    normalized_database = validate_database(database)
    normalized_settings = _validate_settings(settings)
    endpoints = _representative_endpoints(normalized_database, normalized_settings)
    team_count = _team_count(normalized_settings["mode"])
    current_internal: dict[str, Any] | None = None
    best_internal: dict[str, Any] | None = None
    pareto_internal: list[dict[str, Any]] = []
    one_action_plans: list[dict[str, Any]] = []
    counter = _SearchCounter(normalized_settings["search_limit"])
    inventory = _inventory_requirements(normalized_account)

    # A selected set can only add requirements to a one-team endpoint: deleting
    # the other slots from any feasible multi-team solution leaves a feasible
    # one-team solution using no more pulls.  Therefore an endpoint whose exact
    # one-team cost exceeds the budget can never appear in a budget-valid plan.
    # This is a lower-bound filter, not a greedy upgrade decision, so C0->C2
    # paths (including zero-DPS intermediate chains) remain intact.
    candidates: list[tuple[_Endpoint, dict[str, Any]]] = []
    for endpoint in endpoints:
        counter.visit()
        roster = _extend_roster({}, _roster_requirements(endpoint, normalized_settings))
        plan = _plan_summary((endpoint,), roster, inventory, normalized_settings)
        if plan is not None and plan["_cost_cents"] <= normalized_settings["budget_cents"]:
            candidates.append((endpoint, plan))

    # Descending DPS supplies a safe completion upper bound.  Source index
    # resolves ties deterministically; plans themselves are always rebuilt in
    # source order so public keys, action order, and team order retain their
    # established semantics.
    candidates.sort(key=lambda item: (-item[0].dps, item[0].source_index))
    rosters = [_roster_requirements(endpoint, normalized_settings) for endpoint, _ in candidates]
    ranking_cost_limit = max(
        _action_cost_cents(normalized_settings["cost_mode"], "character"),
        _action_cost_cents(normalized_settings["cost_mode"], "weapon"),
    )

    def record_complete_plan(plan: dict[str, Any]) -> None:
        nonlocal current_internal, best_internal
        if plan["_cost_cents"] == 0 and _better_current(plan, current_internal):
            current_internal = plan
        if plan["_cost_cents"] <= normalized_settings["budget_cents"]:
            if _better_best(plan, best_internal):
                best_internal = plan
            _insert_pareto(pareto_internal, plan)
            if plan["gold_count"] == 1 and plan["_cost_cents"] > 0:
                one_action_plans.append(plan)

    def search(
        selected: tuple[_Endpoint, ...], start_index: int, partial_plan: dict[str, Any],
        roster: dict[str, tuple[int, int, int]],
    ) -> None:
        selected_count = len(selected)
        if selected_count == team_count:
            record_complete_plan(partial_plan)
            return

        remaining = team_count - selected_count
        for candidate_index in range(start_index, len(candidates)):
            endpoint = candidates[candidate_index][0]
            upper_dps = partial_plan["_dps"] + endpoint.dps * remaining
            # This shrinking bound applies to every remaining loop entry.
            # Preserve one-action candidates for the separate ROI ranking.
            if (
                partial_plan["_cost_cents"] > ranking_cost_limit
                and _strictly_dominates_upper_bound(pareto_internal, partial_plan["_cost_cents"], upper_dps)
            ):
                break
            child_roster = _extend_roster(roster, rosters[candidate_index])
            if child_roster is None:
                continue
            counter.visit()
            child = tuple(sorted((*selected, endpoint), key=lambda item: item.source_index))
            child_plan = _plan_summary(child, child_roster, inventory, normalized_settings)
            if child_plan is None or child_plan["_cost_cents"] > normalized_settings["budget_cents"]:
                continue
            search(child, candidate_index, child_plan, child_roster)

    for candidate_index, (endpoint, singleton_plan) in enumerate(candidates):
        # The singleton was materialized by the bounded prefilter above, so do
        # not charge it against the state counter a second time.
        search((endpoint,), candidate_index, singleton_plan, _extend_roster({}, rosters[candidate_index]))

    by_index = {endpoint.source_index: endpoint for endpoint in endpoints}

    def materialize(plan: dict[str, Any]) -> dict[str, Any]:
        result = _build_plan(tuple(by_index[index] for index in plan["_key"]), normalized_account, normalized_settings)
        assert result is not None
        return result

    current_internal = materialize(current_internal) if current_internal is not None else None
    best_internal = materialize(best_internal) if best_internal is not None else None
    baseline = current_internal["_dps"] if current_internal is not None else Decimal("0")
    if current_internal is None:
        current = {"feasible": False, "total_dps": 0, "teams": []}
    else:
        current = {
            "feasible": True,
            "total_dps": current_internal["total_dps"],
            "teams": current_internal["teams"],
        }
    cost_mode = normalized_settings["cost_mode"]
    best = _empty_plan(cost_mode) if best_internal is None else _with_gain(best_internal, baseline, cost_mode)
    frontier_internal = _pareto(pareto_internal)
    pareto_frontier = [_with_gain(materialize(plan), baseline, cost_mode) for plan in frontier_internal]
    upgrade_rankings = [
        _with_gain(materialize(plan), baseline, cost_mode)
        for plan in sorted(
            (plan for plan in one_action_plans if plan["_dps"] > baseline),
            key=lambda plan: (
                -(plan["_dps"] - baseline) / Decimal(plan["_cost_cents"]),
                -plan["_dps"],
                plan["_cost_cents"],
                plan["_key"],
            ),
        )
    ]
    upgrade_path = (
        _optimal_upgrade_path(
            normalized_account, [endpoint for endpoint, _ in candidates], normalized_settings,
            best_internal, current_internal, counter,
        )
        if best_internal is not None else []
    )
    return {
        "cost_mode": cost_mode,
        "current": current,
        "best": best,
        "pareto_frontier": pareto_frontier,
        "upgrade_rankings": upgrade_rankings,
        "upgrade_path": upgrade_path,
        "explored_combinations": counter.visited,
        "exact": True,
    }
