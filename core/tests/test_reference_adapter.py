from __future__ import annotations

import pytest

from wuwa_optimizer import ValidationError
from wuwa_optimizer.reference_adapter import prepare_reference_inputs


def account(*, chain: int = 0) -> dict:
    return {
        "characters": [{"character": "漂泊者·导电", "chain": chain}],
        "weapons": [{"id": "owned", "weapon": "Owned weapon", "refinement": 1}],
    }


def database(metadata: dict | None = None) -> dict:
    result = {
        "version": "test",
        "records": [
            {
                "id": "rover-c6",
                "members": [
                    {"character": "漂泊者·导电", "chain": 6},
                    {"character": "Other", "chain": 0},
                    {"character": "Third", "chain": 0},
                ],
            },
            {
                "id": "custom-c6",
                "members": [
                    {"character": "Custom free name", "chain": 6},
                    {"character": "Other", "chain": 0},
                    {"character": "Third", "chain": 0},
                ],
            },
        ],
    }
    if metadata is not None:
        result["metadata"] = metadata
    return result


def test_without_metadata_returns_equal_copies() -> None:
    original_account = account()
    original_database = database()

    prepared_account, prepared_database = prepare_reference_inputs(
        original_account, original_database, assume_common_weapons=True
    )

    assert prepared_account == original_account
    assert prepared_database == original_database
    assert prepared_account is not original_account
    assert prepared_database is not original_database


def test_common_baseline_is_r1_fixed_prefix_only_for_simplified_accounts() -> None:
    source_database = database(
        {"assumed_common_weapons": [{"character": "测试角色", "weapon": "表内常驻·测试角色", "refinement": 1}]}
    )

    with_baseline, _ = prepare_reference_inputs(account(), source_database, assume_common_weapons=True)
    without_baseline, _ = prepare_reference_inputs(account(), source_database, assume_common_weapons=False)

    assert with_baseline["weapons"][-4:] == [
        {"id": f"assumed-common-{index}", "weapon": "表内常驻·测试角色", "refinement": 1}
        for index in range(1, 5)
    ]
    assert without_baseline["weapons"] == account()["weapons"]
    assert source_database["metadata"]["assumed_common_weapons"][0]["refinement"] == 1


@pytest.mark.parametrize(
    "entry",
    [
        {"character": "测试角色", "weapon": "Forged weapon", "refinement": 1},
        {"character": "测试角色", "weapon": "表内常驻·测试角色", "refinement": 5},
    ],
)
def test_common_baseline_rejects_other_weapon_names_and_refinements(entry: dict) -> None:
    with pytest.raises(ValidationError):
        prepare_reference_inputs(account(), database({"assumed_common_weapons": [entry]}), assume_common_weapons=True)


def test_only_declared_fixed_rover_forms_are_filtered_by_explicit_chain() -> None:
    source_database = database({"non_gacha_characters": ["漂泊者·导电", "Custom free name"]})

    _, unowned_database = prepare_reference_inputs(account(chain=5), source_database, assume_common_weapons=False)
    _, owned_database = prepare_reference_inputs(account(chain=6), source_database, assume_common_weapons=False)

    assert [record["id"] for record in unowned_database["records"]] == ["custom-c6"]
    assert [record["id"] for record in owned_database["records"]] == ["rover-c6", "custom-c6"]


def test_aero_rover_requires_the_owned_form_and_all_required_chains() -> None:
    source_database = database({"non_gacha_characters": ["漂泊者·气动"]})
    source_database["records"][0]["members"][0]["character"] = "漂泊者·气动"
    for owned_form, chain, feasible in [
        ("漂泊者·导电", 6, False),
        ("漂泊者·气动", 5, False),
        ("漂泊者·气动", 6, True),
    ]:
        inventory = account(chain=chain)
        inventory["characters"][0]["character"] = owned_form
        _, prepared = prepare_reference_inputs(inventory, source_database, assume_common_weapons=False)
        assert any(record["id"] == "rover-c6" for record in prepared["records"]) is feasible
