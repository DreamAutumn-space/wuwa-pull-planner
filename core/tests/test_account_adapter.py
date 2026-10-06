from __future__ import annotations

from copy import deepcopy

import pytest

from wuwa_optimizer import (
    FOUR_STAR_CHARACTERS,
    ValidationError,
    apply_default_four_stars,
    expand_character_account,
    optimize,
)


def test_signature_accounts_expand_same_weapon_name_to_two_distinct_instances() -> None:
    account = {
        "characters": [
            {"character": "甲", "chain": 2, "signature_refinement": 1},
            {"character": "乙", "chain": 0, "signature_refinement": 5},
            {"character": "丙", "chain": 6, "signature_refinement": 0},
        ]
    }
    original = deepcopy(account)

    expanded = expand_character_account(account, {"甲": "同名专武", "乙": "同名专武"})

    assert expanded["characters"] == [
        {"character": "甲", "chain": 2},
        {"character": "乙", "chain": 0},
        {"character": "丙", "chain": 6},
    ]
    assert expanded["weapons"] == [
        {"id": "signature-甲", "weapon": "同名专武", "refinement": 1},
        {"id": "signature-乙", "weapon": "同名专武", "refinement": 5},
    ]
    assert account == original


@pytest.mark.parametrize(
    "entry",
    [
        {"character": "甲", "chain": True, "signature_refinement": 0},
        {"character": "甲", "chain": 0, "signature_refinement": True},
        {"character": "甲", "chain": 0, "signature_refinement": -1},
        {"character": "甲", "chain": 0, "signature_refinement": 6},
    ],
)
def test_signature_account_rejects_boolean_and_out_of_range_levels(entry: dict) -> None:
    with pytest.raises(ValidationError):
        expand_character_account({"characters": [entry]}, {"甲": "甲专武"})


def test_nonzero_signature_without_exact_catalog_entry_is_explicit() -> None:
    with pytest.raises(ValidationError, match="cannot fabricate a signature weapon"):
        expand_character_account(
            {"characters": [{"character": "未知角色", "chain": 0, "signature_refinement": 1}]},
            {"别名": "某把专武"},
        )


def test_zero_signature_does_not_need_a_catalog_entry() -> None:
    expanded = expand_character_account(
        {"characters": [{"character": "未知角色", "chain": 0, "signature_refinement": 0}]},
        {},
    )
    assert expanded["weapons"] == []


def test_duplicate_character_is_detected_after_whitespace_normalization() -> None:
    with pytest.raises(ValidationError, match="duplicate character"):
        expand_character_account(
            {
                "characters": [
                    {"character": " 甲 ", "chain": 0, "signature_refinement": 0},
                    {"character": "甲", "chain": 0, "signature_refinement": 0},
                ]
            },
            {},
        )


def test_presence_of_weapons_keeps_legacy_manual_inventory_shape() -> None:
    legacy = {
        "characters": [{"character": "甲", "chain": 1, "signature_refinement": 5}],
        "weapons": [{"id": "manual-1", "weapon": "自定义武器", "refinement": 3}],
    }

    expanded = expand_character_account(legacy, {})

    assert expanded == {
        "characters": [{"character": "甲", "chain": 1}],
        "weapons": [{"id": "manual-1", "weapon": "自定义武器", "refinement": 3}],
    }


def test_default_four_stars_overwrite_only_their_own_chain_and_signature_without_mutation() -> None:
    source = {
        "characters": [
            {"character": "秧秧", "chain": 1, "signature_refinement": 5},
            {"character": "今汐", "chain": 2, "signature_refinement": 4},
        ]
    }
    original = deepcopy(source)

    completed = apply_default_four_stars(source)
    by_name = {entry["character"]: entry for entry in completed["characters"]}

    assert tuple(FOUR_STAR_CHARACTERS) == ("秧秧", "白芷", "炽霞", "丹瑾", "莫特斐", "桃祈", "渊武", "散华", "秋水", "釉瑚", "灯灯", "卜灵")
    assert by_name["秧秧"] == {"character": "秧秧", "chain": 6, "signature_refinement": 0}
    assert by_name["白芷"] == {"character": "白芷", "chain": 6, "signature_refinement": 0}
    assert by_name["今汐"] == {"character": "今汐", "chain": 2, "signature_refinement": 4}
    assert source == original


def test_default_four_star_c6_prevents_additional_character_cost_in_optimizer() -> None:
    simplified = {
        "characters": [
            {"character": "甲", "chain": 0, "signature_refinement": 0},
            {"character": "乙", "chain": 0, "signature_refinement": 0},
        ]
    }
    account = expand_character_account(apply_default_four_stars(simplified), {})
    database = {
        "version": "test",
        "records": [
            {
                "id": "four-star-c6",
                "main_c": "秧秧",
                "members": [
                    {"character": "秧秧", "chain": 6, "weapon": "武器甲", "refinement": 1},
                    {"character": "甲", "chain": 0, "weapon": "武器乙", "refinement": 1},
                    {"character": "乙", "chain": 0, "weapon": "武器丙", "refinement": 1},
                ],
                "rotations": [{"id": "middle", "difficulty": "中", "dps": 100}],
            }
        ],
    }

    result = optimize(account, database, {"mode": "single", "budget": 200})

    assert result["best"]["cost"] == 162.33  # only three missing physical weapons
    assert not any(action["kind"].startswith("character") for action in result["best"]["actions"])


def test_default_four_stars_preserve_legacy_weapon_inventory_and_five_star_chain() -> None:
    legacy = {
        "characters": [{"character": "秧秧", "chain": 0}, {"character": "今汐", "chain": 3}],
        "weapons": [{"id": "actual-inventory", "weapon": "四星武器", "refinement": 5}],
    }
    original = deepcopy(legacy)

    completed = apply_default_four_stars(legacy)
    by_name = {entry["character"]: entry for entry in completed["characters"]}

    assert by_name["秧秧"]["chain"] == 6
    assert by_name["今汐"]["chain"] == 3
    assert completed["weapons"] == legacy["weapons"]
    assert legacy == original


@pytest.mark.parametrize(
    "account",
    [
        {"characters": [{"character": "秧秧", "chain": True, "signature_refinement": 0}]},
        {"characters": [{"character": "秧秧", "chain": 0, "signature_refinement": True}]},
        {"characters": [{"character": "秧秧", "chain": True}], "weapons": []},
    ],
)
def test_default_four_stars_rejects_malformed_input_before_policy(account: dict) -> None:
    with pytest.raises(ValidationError):
        apply_default_four_stars(account)
