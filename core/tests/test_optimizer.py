from __future__ import annotations

from itertools import combinations_with_replacement
import math

import pytest

import wuwa_optimizer.optimizer as optimizer_module
from wuwa_optimizer import (
    SearchLimitError,
    ValidationError,
    optimize,
    settings_from_json,
    validate_account,
    validate_database,
)


def member(
    character: str,
    chain: int = 0,
    weapon: str | None = None,
    refinement: int = 1,
    max_chain: int | None = None,
) -> dict:
    result = {
        "character": character,
        "chain": chain,
        "weapon": weapon or f"{character}-weapon",
        "refinement": refinement,
    }
    if max_chain is not None:
        result["max_chain"] = max_chain
    return result


def record(record_id: str, main_c: str, members: list[dict], rotations: list[dict] | None = None) -> dict:
    return {
        "id": record_id,
        "main_c": main_c,
        "members": members,
        "rotations": rotations or [{"id": f"{record_id}-mid", "difficulty": "中", "dps": 100}],
    }


def db(*records_: dict) -> dict:
    return {"version": "test", "records": list(records_)}


def account_for(records_: list[dict]) -> dict:
    """Create sufficient C0/R1 assets for every fixture record."""
    characters: dict[str, int] = {}
    weapons: dict[str, dict] = {}
    for team in records_:
        for item in team["members"]:
            characters[item["character"]] = max(characters.get(item["character"], 0), item["chain"])
            weapons.setdefault(
                item["weapon"],
                {"id": f"owned-{item['weapon']}", "weapon": item["weapon"], "refinement": item["refinement"]},
            )
    return {
        "characters": [{"character": name, "chain": chain} for name, chain in characters.items()],
        "weapons": list(weapons.values()),
    }


def settings(**overrides: object) -> dict:
    base = {"mode": "single", "budget": 0, "max_difficulty": "中"}
    base.update(overrides)
    return base


def team(record_id: str, main_c: str, dps: int) -> dict:
    return record(
        record_id,
        main_c,
        [member(main_c), member(f"{record_id}-support-1"), member(f"{record_id}-support-2")],
        [{"id": f"{record_id}-mid", "difficulty": "中", "dps": dps}],
    )


def brute_force_optimize(account: dict, database: dict, request_settings: dict) -> dict:
    """The former leaf-only enumeration, used as an oracle for small fixtures."""
    normalized_account = validate_account(account)
    normalized_database = validate_database(database)
    normalized_settings = optimizer_module._validate_settings(request_settings)
    endpoints = optimizer_module._representative_endpoints(normalized_database, normalized_settings)
    team_count = optimizer_module._team_count(normalized_settings["mode"])

    plans = []
    for combination in combinations_with_replacement(range(len(endpoints)), team_count):
        plan = optimizer_module._build_plan(
            tuple(endpoints[index] for index in combination), normalized_account, normalized_settings
        )
        if plan is not None:
            plans.append(plan)

    current_internal = None
    best_internal = None
    budgeted = []
    for plan in plans:
        if plan["_cost_cents"] == 0 and optimizer_module._better_current(plan, current_internal):
            current_internal = plan
        if plan["_cost_cents"] <= normalized_settings["budget_cents"]:
            budgeted.append(plan)
            if optimizer_module._better_best(plan, best_internal):
                best_internal = plan

    baseline = current_internal["_dps"] if current_internal is not None else optimizer_module.Decimal("0")
    current = (
        {"feasible": False, "total_dps": 0, "teams": []}
        if current_internal is None
        else {
            "feasible": True,
            "total_dps": current_internal["total_dps"],
            "teams": current_internal["teams"],
        }
    )
    cost_mode = normalized_settings["cost_mode"]
    best = (
        optimizer_module._empty_plan(cost_mode)
        if best_internal is None
        else optimizer_module._with_gain(best_internal, baseline, cost_mode)
    )
    pareto = [
        optimizer_module._with_gain(plan, baseline, cost_mode)
        for plan in optimizer_module._pareto(budgeted)
    ]
    rankings = [
        optimizer_module._with_gain(plan, baseline, cost_mode)
        for plan in sorted(
            (plan for plan in budgeted if len(plan["actions"]) == 1 and plan["_cost_cents"] > 0),
            key=lambda plan: (
                -(plan["_dps"] - baseline) / optimizer_module.Decimal(plan["_cost_cents"]),
                -plan["_dps"],
                plan["_cost_cents"],
                plan["_key"],
            ),
        )
    ]
    return {"current": current, "best": best, "pareto_frontier": pareto, "upgrade_rankings": rankings}


def test_zero_gain_prerequisite_is_kept_for_two_chain_endpoint() -> None:
    c0 = record("c0", "C", [member("C", 0), member("S1"), member("S2")], [{"id": "c0", "difficulty": "中", "dps": 100}])
    c1 = record("c1", "C", [member("C", 1), member("S1"), member("S2")], [{"id": "c1", "difficulty": "中", "dps": 100}])
    c2 = record("c2", "C", [member("C", 2), member("S1"), member("S2")], [{"id": "c2", "difficulty": "中", "dps": 200}])
    inventory = account_for([c0])

    # Older records omit the upper bound and retain C6 as their default.
    assert validate_database(db(c2))["records"][0]["members"][0]["max_chain"] == 6

    result = optimize(inventory, db(c0, c1, c2), settings(budget=162.30))

    assert result["current"]["total_dps"] == 100
    assert result["best"]["total_dps"] == 200
    assert result["best"]["cost"] == 162.30
    assert [(action["from_chain"], action["to_chain"]) for action in result["best"]["actions"]] == [(0, 1), (1, 2)]
    assert [plan["total_dps"] for plan in result["pareto_frontier"]] == [100, 200]


def test_high_owned_chain_cannot_use_lower_max_chain_record() -> None:
    capped = record(
        "capped",
        "C",
        [member("C", max_chain=5), member("S1"), member("S2")],
        [{"id": "capped", "difficulty": "中", "dps": 200}],
    )
    allowed = record(
        "allowed",
        "C",
        [member("C", max_chain=6), member("S1"), member("S2")],
        [{"id": "allowed", "difficulty": "中", "dps": 100}],
    )
    inventory = account_for([capped, allowed])
    inventory["characters"] = [
        {"character": "C", "chain": 6},
        {"character": "S1", "chain": 0},
        {"character": "S2", "chain": 0},
    ]

    result = optimize(inventory, db(capped, allowed), settings(budget=0))

    assert result["current"]["total_dps"] == 100
    assert result["best"]["teams"][0]["record_id"] == "allowed"


def test_shared_healer_chain_upgrade_must_fit_every_selected_max_chain() -> None:
    first = record(
        "first",
        "A",
        [member("A"), member("H", chain=3, weapon="H-weapon", max_chain=6), member("A2")],
        [{"id": "first", "difficulty": "中", "dps": 200}],
    )
    blocked = record(
        "blocked",
        "B",
        [member("B"), member("H", weapon="H-weapon", max_chain=2), member("B2")],
        [{"id": "blocked", "difficulty": "中", "dps": 110}],
    )
    allowed = record(
        "allowed",
        "B",
        [member("B"), member("H", weapon="H-weapon", max_chain=3), member("B2")],
        [{"id": "allowed", "difficulty": "中", "dps": 100}],
    )
    inventory = account_for([first, blocked, allowed])
    inventory["characters"] = [
        {**asset, "chain": 0} if asset["character"] == "H" else asset
        for asset in inventory["characters"]
    ]
    inventory["weapons"].append({"id": "owned-H-weapon-2", "weapon": "H-weapon", "refinement": 1})

    result = optimize(
        inventory,
        db(first, blocked, allowed),
        settings(
            mode="two_teams",
            budget=243.45,
            repeatable_healers=["H"],
            healer_capacity=2,
        ),
    )

    assert {team["record_id"] for team in result["best"]["teams"]} == {"first", "allowed"}
    assert result["best"]["cost"] == 243.45
    assert [
        (action["from_chain"], action["to_chain"])
        for action in result["best"]["actions"]
        if action.get("character") == "H"
    ] == [(0, 1), (1, 2), (2, 3)]


def test_joint_two_and_four_team_optimization_respects_non_repeatable_characters() -> None:
    a = team("a", "A", 100)
    alternate_a = team("a-alt", "A", 99)
    b = team("b", "B", 90)
    c = team("c", "C", 80)
    d = team("d", "D", 70)
    records_ = [a, alternate_a, b, c, d]
    inventory = account_for(records_)

    two = optimize(inventory, db(*records_), settings(mode="two_teams", budget=0))
    four = optimize(inventory, db(*records_), settings(mode="four_teams", budget=0))

    assert two["best"]["total_dps"] == 190
    assert {entry["main_c"] for entry in two["best"]["teams"]} == {"A", "B"}
    assert four["best"]["total_dps"] == 340
    assert {entry["main_c"] for entry in four["best"]["teams"]} == {"A", "B", "C", "D"}
    assert four["exact"] is True


def test_joint_optimizer_can_skip_the_strongest_single_team_for_better_packing() -> None:
    top = record("top", "X", [member("X"), member("Y"), member("T")], [{"id": "top", "difficulty": "中", "dps": 1000}])
    left = record("left", "X", [member("X"), member("A"), member("B")], [{"id": "left", "difficulty": "中", "dps": 600}])
    right = record("right", "Y", [member("Y"), member("C"), member("D")], [{"id": "right", "difficulty": "中", "dps": 600}])
    filler_one = team("filler-one", "E", 190)
    filler_two = team("filler-two", "F", 180)
    filler_three = team("filler-three", "G", 100)
    records_ = [top, left, right, filler_one, filler_two, filler_three]
    inventory = account_for(records_)

    two = optimize(inventory, db(*records_), settings(mode="two_teams", budget=0))
    four = optimize(inventory, db(*records_), settings(mode="four_teams", budget=0))

    assert two["best"]["total_dps"] == 1200
    assert {item["record_id"] for item in two["best"]["teams"]} == {"left", "right"}
    assert four["best"]["total_dps"] == 1570
    assert {item["record_id"] for item in four["best"]["teams"]} == {
        "left",
        "right",
        "filler-one",
        "filler-two",
    }


def test_only_whitelisted_healer_can_repeat_and_it_still_needs_two_weapon_instances() -> None:
    first = record("first", "A", [member("A"), member("H", weapon="H-weapon"), member("A2")])
    second = record("second", "B", [member("B"), member("H", weapon="H-weapon"), member("B2")])
    inventory = account_for([first, second])
    inventory["weapons"].append({"id": "owned-H-weapon-2", "weapon": "H-weapon", "refinement": 1})

    blocked = optimize(inventory, db(first, second), settings(mode="two_teams", budget=0))
    allowed = optimize(
        inventory,
        db(first, second),
        settings(mode="two_teams", budget=0, repeatable_healers=["H"], healer_capacity=2),
    )

    assert blocked["best"]["feasible"] is False
    assert allowed["current"]["feasible"] is True
    healer_weapon_ids = [
        assignment["instance_id"]
        for selected_team in allowed["current"]["teams"]
        for assignment in selected_team["weapon_assignment"]
        if assignment["character"] == "H"
    ]
    assert len(healer_weapon_ids) == len(set(healer_weapon_ids)) == 2


def test_default_healer_whitelist_applies_only_when_the_field_is_omitted() -> None:
    first = record("first", "A", [member("A"), member("守岸人", weapon="shorekeeper-weapon"), member("A2")])
    second = record("second", "B", [member("B"), member("守岸人", weapon="shorekeeper-weapon"), member("B2")])
    inventory = account_for([first, second])
    inventory["weapons"].append({"id": "shorekeeper-weapon-2", "weapon": "shorekeeper-weapon", "refinement": 1})

    default_settings = settings(mode="two_teams", budget=0)
    defaulted = optimize(inventory, db(first, second), default_settings)
    explicitly_disabled = optimize(
        inventory,
        db(first, second),
        settings(mode="two_teams", budget=0, repeatable_healers=[]),
    )

    assert settings_from_json(default_settings).repeatable_healers == frozenset(
        {"守岸人", "维里奈", "莫宁", "卜灵", "白芷"}
    )
    assert defaulted["current"]["feasible"] is True
    assert explicitly_disabled["best"]["feasible"] is False


def test_repeatable_healer_is_limited_to_two_teams_and_legacy_capacities_normalize() -> None:
    first = record("first", "A", [member("A"), member("守岸人", weapon="shorekeeper-weapon"), member("A2")])
    second = record("second", "B", [member("B"), member("守岸人", weapon="shorekeeper-weapon"), member("B2")])
    third = record("third", "C", [member("C"), member("守岸人", weapon="shorekeeper-weapon"), member("C2")])
    fourth = team("fourth", "D", 90)
    inventory = account_for([first, second, third, fourth])
    inventory["weapons"].append({"id": "shorekeeper-weapon-2", "weapon": "shorekeeper-weapon", "refinement": 1})
    inventory["weapons"].append({"id": "shorekeeper-weapon-3", "weapon": "shorekeeper-weapon", "refinement": 1})

    result = optimize(inventory, db(first, second, third, fourth), settings(mode="four_teams", budget=0, healer_capacity=4))

    assert settings_from_json(settings(healer_capacity=1)).healer_capacity == 2
    assert settings_from_json(settings(healer_capacity=3)).healer_capacity == 2
    assert settings_from_json(settings(healer_capacity=4)).healer_capacity == 2
    assert result["best"]["feasible"] is False
    with pytest.raises(ValidationError, match="healer_capacity must be an integer"):
        settings_from_json(settings(healer_capacity=True))


def test_weapon_matching_uses_distinct_instances_and_opposite_ranks_correctly() -> None:
    endpoint = record(
        "rank-match",
        "A",
        [member("A", weapon="X", refinement=2), member("B", weapon="X", refinement=5), member("C", weapon="Y")],
    )
    inventory = {
        "characters": [{"character": name, "chain": 0} for name in ("A", "B", "C")],
        "weapons": [
            {"id": "x-low", "weapon": "X", "refinement": 1},
            {"id": "x-high", "weapon": "X", "refinement": 4},
            {"id": "y", "weapon": "Y", "refinement": 1},
        ],
    }

    result = optimize(inventory, db(endpoint), settings(budget=108.22))

    assignments = {item["character"]: item for item in result["best"]["teams"][0]["weapon_assignment"]}
    assert assignments["A"]["instance_id"] == "x-low"
    assert assignments["B"]["instance_id"] == "x-high"
    assert result["best"]["cost"] == 108.22
    assert [action["instance_id"] for action in result["best"]["actions"]] == ["x-low", "x-high"]
    assert optimize(inventory, db(endpoint), settings(budget=108.21))["best"]["feasible"] is False


def test_costs_are_prerequisite_aware_and_use_integer_cents() -> None:
    endpoint = record(
        "costs",
        "C",
        [member("C", 2, "X", 3), member("S1", 0, "Y", 1), member("S2", 0, "Z", 1)],
    )

    result = optimize({"characters": [], "weapons": []}, db(endpoint), settings(budget=676.30))

    assert result["current"]["feasible"] is False
    assert result["best"]["cost"] == 676.30  # 5 character + 5 weapon pulls
    assert result["best"]["gain_percent"] is None
    character_actions = [action for action in result["best"]["actions"] if action["kind"].startswith("character")]
    assert [(action["character"], action["to_chain"]) for action in character_actions] == [
        ("C", 0),
        ("C", 1),
        ("C", 2),
        ("S1", 0),
        ("S2", 0),
    ]
    assert result["best"]["roi_per_100_pulls"] > 0


def test_high_owned_chains_and_refinements_are_never_charged_twice() -> None:
    endpoint = record(
        "owned-high",
        "C",
        [member("C", 2, "X", 3), member("S1", 0, "Y", 1), member("S2", 0, "Z", 1)],
    )
    high_inventory = {
        "characters": [{"character": "C", "chain": 3}, {"character": "S1", "chain": 0}, {"character": "S2", "chain": 0}],
        "weapons": [
            {"id": "x", "weapon": "X", "refinement": 5},
            {"id": "y", "weapon": "Y", "refinement": 1},
            {"id": "z", "weapon": "Z", "refinement": 1},
        ],
    }
    c1_inventory = {**high_inventory, "characters": [{"character": "C", "chain": 1}, {"character": "S1", "chain": 0}, {"character": "S2", "chain": 0}]}

    already_owned = optimize(high_inventory, db(endpoint), settings(budget=0))
    one_chain_short = optimize(c1_inventory, db(endpoint), settings(budget=81.15))

    assert already_owned["current"]["feasible"] is True
    assert already_owned["best"]["cost"] == 0
    assert already_owned["best"]["actions"] == []
    assert one_chain_short["best"]["cost"] == 81.15
    assert [(action["from_chain"], action["to_chain"]) for action in one_chain_short["best"]["actions"]] == [(1, 2)]


def test_difficulty_filter_uses_best_rotation_among_permitted_axes() -> None:
    endpoint = record(
        "axes",
        "A",
        [member("A"), member("B"), member("C")],
        [
            {"id": "low", "difficulty": "低", "dps": 100},
            {"id": "mid", "difficulty": "中", "dps": 120},
            {"id": "high", "difficulty": "高", "dps": 999},
        ],
    )
    inventory = account_for([endpoint])

    capped = optimize(inventory, db(endpoint), settings(budget=0, max_difficulty="中"))
    custom = optimize(
        inventory,
        db(endpoint),
        settings(budget=0, max_difficulty="低", allowed_difficulties=["低", "高"]),
    )

    assert capped["best"]["total_dps"] == 120
    assert capped["best"]["teams"][0]["rotation_id"] == "mid"
    assert custom["best"]["total_dps"] == 999
    assert custom["best"]["teams"][0]["difficulty"] == "高"


def test_main_c_and_fixed_team_modes_constrain_candidates() -> None:
    alpha = team("alpha", "Alpha", 200)
    beta = team("beta", "Beta", 100)
    inventory = account_for([alpha, beta])

    by_main_c = optimize(inventory, db(alpha, beta), settings(mode="main_c", target_main_c="Beta", budget=0))
    fixed = optimize(
        inventory,
        db(alpha, beta),
        settings(mode="fixed_team", target_team=["Beta", "beta-support-1", "beta-support-2"], budget=0),
    )

    assert by_main_c["best"]["teams"][0]["record_id"] == "beta"
    assert fixed["best"]["teams"][0]["record_id"] == "beta"


def test_member_max_chain_must_not_be_below_required_chain() -> None:
    invalid = record(
        "invalid-max-chain",
        "A",
        [member("A", chain=3, max_chain=2), member("B"), member("C")],
    )

    with pytest.raises(ValidationError, match="max_chain"):
        validate_database(db(invalid))


@pytest.mark.parametrize(
    ("account", "database"),
    [
        (
            {"characters": [], "weapons": [{"id": "same", "weapon": "X", "refinement": 1}, {"id": "same", "weapon": "X", "refinement": 2}]},
            None,
        ),
        (
            {"characters": [{"character": "A", "chain": True}], "weapons": []},
            None,
        ),
        (
            None,
            db(
                record(
                    "nan",
                    "A",
                    [member("A"), member("B"), member("C")],
                    [{"id": "bad", "difficulty": "中", "dps": math.nan}],
                )
            ),
        ),
        (
            None,
            db(
                {
                    "id": "not-three",
                    "main_c": "A",
                    "members": [member("A"), member("B")],
                    "rotations": [{"id": "r", "difficulty": "中", "dps": 1}],
                }
            ),
        ),
    ],
)
def test_invalid_account_and_database_values_are_rejected(account: dict | None, database: dict | None) -> None:
    if account is not None:
        with pytest.raises(ValidationError):
            validate_account(account)
    if database is not None:
        with pytest.raises(ValidationError):
            validate_database(database)


def test_infeasible_requires_the_exact_requested_number_of_teams() -> None:
    endpoint = team("only", "A", 100)
    inventory = account_for([endpoint])

    result = optimize(inventory, db(endpoint), settings(mode="two_teams", budget=9_999))

    assert result["current"] == {"feasible": False, "total_dps": 0, "teams": []}
    assert result["best"]["feasible"] is False
    assert result["pareto_frontier"] == []
    # One retained singleton plus its rejected repeated-child state were
    # actually materialized.  The counter measures states, not a speculative
    # binomial number of leaves.
    assert result["explored_combinations"] == 2


def test_search_limit_never_returns_an_approximation() -> None:
    first = team("first", "A", 100)
    second = team("second", "B", 90)
    inventory = account_for([first, second])

    with pytest.raises(SearchLimitError, match="visited 3 states, exceeding search_limit=2"):
        optimize(inventory, db(first, second), settings(mode="two_teams", budget=0, search_limit=2))


def test_branch_and_bound_matches_leaf_enumeration_for_every_team_mode() -> None:
    records_ = [
        record("a", "A", [member("A"), member("H", weapon="H-weapon"), member("A2")], [{"id": "a", "difficulty": "中", "dps": 100}]),
        record("b", "B", [member("B"), member("H", weapon="H-weapon"), member("B2")], [{"id": "b", "difficulty": "中", "dps": 90}]),
        record("c", "C", [member("C", chain=1), member("H", weapon="H-weapon"), member("C2")], [{"id": "c", "difficulty": "中", "dps": 110}]),
        record("d", "D", [member("D"), member("H", weapon="H-weapon"), member("D2")], [{"id": "d", "difficulty": "中", "dps": 80}]),
        record("e", "E", [member("E"), member("E1"), member("E2")], [{"id": "e", "difficulty": "中", "dps": 70}]),
        record("f", "F", [member("F"), member("F1"), member("F2")], [{"id": "f", "difficulty": "中", "dps": 60}]),
    ]
    inventory = account_for(records_)
    inventory["characters"] = [
        {**asset, "chain": 0} if asset["character"] == "C" else asset
        for asset in inventory["characters"]
    ]

    for mode in ("single", "two_teams", "four_teams"):
        request_settings = settings(
            mode=mode,
            budget=1_000,
            repeatable_healers=["H"],
            healer_capacity=4,
            search_limit=10_000,
        )
        assert optimizer_module._validate_settings(request_settings)["healer_capacity"] == 2
        actual = optimize(inventory, db(*records_), request_settings)
        expected = brute_force_optimize(inventory, db(*records_), request_settings)

        assert {key: actual[key] for key in expected} == expected
        assert actual["exact"] is True


def test_dps_upper_bound_prunes_dominated_expensive_branches_exactly() -> None:
    high_a = team("high-a", "A", 1_000)
    high_b = team("high-b", "B", 900)
    expensive_low = [
        record(
            f"low-{index}",
            f"L{index}",
            [
                member(f"L{index}", chain=6, refinement=5),
                member(f"L{index}-s1", chain=6, refinement=5),
                member(f"L{index}-s2", chain=6, refinement=5),
            ],
            [{"id": f"low-{index}", "difficulty": "中", "dps": 100 - index}],
        )
        for index in range(8)
    ]
    records_ = [high_a, high_b, *expensive_low]
    inventory = account_for([high_a, high_b])
    request_settings = settings(mode="two_teams", budget=2_000, search_limit=1_000)

    actual = optimize(inventory, db(*records_), request_settings)
    expected = brute_force_optimize(inventory, db(*records_), request_settings)

    assert {key: actual[key] for key in expected} == expected
    # There are C(11, 2) = 55 full leaves.  The high, free A+B plan strictly
    # dominates every low branch before their second team slot is materialized.
    assert actual["explored_combinations"] < 55


def test_budget_impossible_endpoints_are_preexcluded_before_combination_search() -> None:
    records_ = [
        record(
            f"unaffordable-{index}",
            f"A{index}",
            [member(f"A{index}", chain=6, refinement=5), member(f"B{index}", chain=6, refinement=5), member(f"C{index}", chain=6, refinement=5)],
            [{"id": f"unaffordable-{index}", "difficulty": "中", "dps": 100 + index}],
        )
        for index in range(80)
    ]

    result = optimize(
        {"characters": [], "weapons": []},
        db(*records_),
        settings(mode="four_teams", budget=0, search_limit=100),
    )

    assert result["best"]["feasible"] is False
    assert result["pareto_frontier"] == []
    # The old upfront C(83, 4) guard would reject this input despite every
    # endpoint being individually over budget.  Exact lower-bound filtering
    # examines only these 80 concrete singleton states.
    assert result["explored_combinations"] == 80


def test_gold_mode_charges_every_character_and_weapon_action_equally() -> None:
    character_upgrade = record(
        "character-upgrade",
        "A",
        [member("A", chain=1), member("B"), member("C")],
        [{"id": "character-upgrade-mid", "difficulty": "中", "dps": 150}],
    )
    weapon_upgrade = record(
        "weapon-upgrade",
        "D",
        [member("D", weapon="D-weapon", refinement=2), member("E"), member("F")],
        [{"id": "weapon-upgrade-mid", "difficulty": "中", "dps": 160}],
    )
    inventory = account_for([character_upgrade, weapon_upgrade])
    inventory["characters"] = [
        {**asset, "chain": 0} if asset["character"] == "A" else asset
        for asset in inventory["characters"]
    ]
    inventory["weapons"] = [
        {**weapon, "refinement": 1} if weapon["weapon"] == "D-weapon" else weapon
        for weapon in inventory["weapons"]
    ]

    result = optimize(
        inventory,
        db(character_upgrade, weapon_upgrade),
        settings(cost_mode="gold", budget=1),
    )

    assert result["cost_mode"] == "gold"
    assert result["best"]["teams"][0]["record_id"] == "weapon-upgrade"
    assert result["best"]["cost"] == 1
    assert result["best"]["gold_count"] == 1
    assert result["best"]["actions"][0]["cost"] == 1
    assert result["best"]["roi_per_gold"] == 160
    assert result["best"]["roi_per_100_pulls"] is None


def test_gold_mode_refinement_prerequisites_cost_one_gold_each_and_only_missing_ranks() -> None:
    endpoint = record(
        "r5",
        "A",
        [member("A", weapon="X", refinement=5), member("B", weapon="Y"), member("C", weapon="Z")],
    )
    inventory = account_for([endpoint])
    inventory["weapons"] = [weapon for weapon in inventory["weapons"] if weapon["weapon"] != "X"]

    from_empty = optimize(inventory, db(endpoint), settings(cost_mode="gold", budget=5))
    inventory["weapons"].append({"id": "x-r2", "weapon": "X", "refinement": 2})
    from_r2 = optimize(inventory, db(endpoint), settings(cost_mode="gold", budget=3))

    assert from_empty["best"]["cost"] == 5
    assert from_empty["best"]["gold_count"] == 5
    assert [action["to_refinement"] for action in from_empty["best"]["actions"]] == [1, 2, 3, 4, 5]
    assert from_r2["best"]["cost"] == 3
    assert from_r2["best"]["gold_count"] == 3
    assert [action["from_refinement"] for action in from_r2["best"]["actions"]] == [2, 3, 4]


def test_gold_mode_keeps_a_zero_gain_prerequisite_path() -> None:
    c0 = record("c0", "A", [member("A", 0), member("B"), member("C")], [{"id": "c0", "difficulty": "中", "dps": 100}])
    c1 = record("c1", "A", [member("A", 1), member("B"), member("C")], [{"id": "c1", "difficulty": "中", "dps": 100}])
    c2 = record("c2", "A", [member("A", 2), member("B"), member("C")], [{"id": "c2", "difficulty": "中", "dps": 200}])
    inventory = account_for([c0])

    result = optimize(inventory, db(c0, c1, c2), settings(cost_mode="gold", budget=2))

    assert result["current"]["total_dps"] == 100
    assert result["best"]["total_dps"] == 200
    assert result["best"]["cost"] == 2
    assert [(action["from_chain"], action["to_chain"]) for action in result["best"]["actions"]] == [(0, 1), (1, 2)]


@pytest.mark.parametrize("invalid_budget", [1.5, -1, math.nan])
def test_gold_mode_rejects_non_integer_negative_and_non_finite_budgets(invalid_budget: float) -> None:
    with pytest.raises(ValidationError):
        settings_from_json(settings(cost_mode="gold", budget=invalid_budget))


def test_character_mode_filters_by_membership_and_keeps_zero_budget_current() -> None:
    support_target = record(
        "support-target",
        "A",
        [member("A"), member("指定角色"), member("B")],
        [{"id": "support-target-mid", "difficulty": "中", "dps": 100}],
    )
    unrelated = record(
        "unrelated",
        "指定角色以外主C",
        [member("指定角色以外主C"), member("C"), member("D")],
        [{"id": "unrelated-mid", "difficulty": "中", "dps": 999}],
    )
    inventory = account_for([support_target, unrelated])

    result = optimize(
        inventory,
        db(support_target, unrelated),
        settings(mode="character", target_character="指定角色", budget=0),
    )

    assert result["current"]["feasible"] is True
    assert result["current"]["total_dps"] == 100
    assert result["best"]["teams"][0]["record_id"] == "support-target"


def test_gold_mode_branch_and_bound_matches_exhaustive_oracle() -> None:
    records_ = [
        record("a", "A", [member("A"), member("H", weapon="H-weapon"), member("A2")], [{"id": "a", "difficulty": "中", "dps": 100}]),
        record("b", "B", [member("B"), member("H", weapon="H-weapon"), member("B2")], [{"id": "b", "difficulty": "中", "dps": 90}]),
        record("c", "C", [member("C", chain=1), member("H", weapon="H-weapon"), member("C2")], [{"id": "c", "difficulty": "中", "dps": 110}]),
        record("d", "D", [member("D"), member("D1"), member("D2")], [{"id": "d", "difficulty": "中", "dps": 80}]),
    ]
    inventory = account_for(records_)
    inventory["characters"] = [
        {**asset, "chain": 0} if asset["character"] == "C" else asset
        for asset in inventory["characters"]
    ]
    request_settings = settings(
        mode="two_teams",
        cost_mode="gold",
        budget=2,
        repeatable_healers=["H"],
        search_limit=10_000,
    )

    actual = optimize(inventory, db(*records_), request_settings)
    expected = brute_force_optimize(inventory, db(*records_), request_settings)

    assert {key: actual[key] for key in expected} == expected
    assert actual["exact"] is True
