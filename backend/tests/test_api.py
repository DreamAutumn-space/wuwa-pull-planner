from __future__ import annotations

import json
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from fastapi.testclient import TestClient

from app.main import JSON_LIMIT_BYTES, RuntimeConfig, create_app


def sample_database() -> dict:
    return {
        "version": "test",
        "records": [
            {
                "id": "team-a",
                "main_c": "Jinhsi",
                "members": [
                    {"character": "Jinhsi", "chain": 0, "weapon": "Test weapon", "refinement": 1},
                    {"character": "Test sub-DPS", "chain": 0, "weapon": "Test weapon", "refinement": 1},
                    {"character": "Test healer", "chain": 0, "weapon": "Test weapon", "refinement": 1},
                ],
                "rotations": [{"id": "normal", "difficulty": "中", "dps": 100}],
            }
        ],
    }


def sample_account() -> dict:
    return {
        "characters": [
            {"character": "Jinhsi", "chain": 0},
            {"character": "Test sub-DPS", "chain": 0},
            {"character": "Test healer", "chain": 0},
        ],
        "weapons": [
            {"id": "test-weapon-1", "weapon": "Test weapon", "refinement": 1},
            {"id": "test-weapon-2", "weapon": "Test weapon", "refinement": 1},
            {"id": "test-weapon-3", "weapon": "Test weapon", "refinement": 1},
        ],
    }


def sample_settings() -> dict:
    return {"mode": "single", "budget": 0, "max_difficulty": "中"}


def test_gold_budget_and_support_character_filter_work_through_http(tmp_path: Path) -> None:
    from wuwa_optimizer import optimize

    database = sample_database()
    weapon_upgrade = json.loads(json.dumps(database["records"][0]))
    weapon_upgrade["id"] = "weapon-upgrade"
    weapon_upgrade["members"][0]["refinement"] = 2
    weapon_upgrade["rotations"][0]["dps"] = 160
    chain_upgrade = json.loads(json.dumps(database["records"][0]))
    chain_upgrade["id"] = "chain-upgrade"
    chain_upgrade["members"][0]["chain"] = 1
    chain_upgrade["rotations"][0]["dps"] = 150
    database["records"].extend([weapon_upgrade, chain_upgrade])

    with make_client(tmp_path, optimize) as client:
        response = client.post("/api/optimize", json={
            "account": sample_account(),
            "database": database,
            "settings": {
                **sample_settings(), "mode": "character", "target_character": "Test healer",
                "cost_mode": "gold", "budget": 1,
            },
        })
        assert response.status_code == 200
        result = response.json()
        assert result["cost_mode"] == "gold"
        assert result["current"]["total_dps"] == 100
        assert result["best"]["total_dps"] == 160
        assert result["best"]["cost"] == 1
        assert result["best"]["roi_per_gold"] == 60
        assert result["best"]["teams"][0]["record_id"] == "weapon-upgrade"
        assert result["upgrade_path"][0]["action"]["kind"] == "weapon_refinement"
        assert result["upgrade_path"][0]["total_dps"] == 160
        assert result["upgrade_path"][0]["gain_percent"] == 60
        assert result["upgrade_path"][0]["teams"] == result["best"]["teams"]


def test_fractional_gold_budget_returns_http_validation_error(tmp_path: Path) -> None:
    from wuwa_optimizer import optimize

    with make_client(tmp_path, optimize) as client:
        response = client.post("/api/optimize", json={
            "account": sample_account(),
            "settings": {**sample_settings(), "cost_mode": "gold", "budget": 1.5},
        })
        assert response.status_code == 422


def make_client(tmp_path: Path, optimizer, **config_overrides):
    (tmp_path / "demo-dps.json").write_text(json.dumps(sample_database()), encoding="utf-8")
    (tmp_path / "demo-request.json").write_text(
        json.dumps({"account": sample_account(), "settings": sample_settings()}), encoding="utf-8"
    )
    config = RuntimeConfig(optimizer_workers=1, database_filename="demo-dps.json", **config_overrides)
    return TestClient(create_app(data_dir=tmp_path, config=config, optimizer=optimizer, use_process_pool=False))


def write_signature_catalog(tmp_path: Path, characters: list[dict]) -> None:
    (tmp_path / "signature-weapons.json").write_text(
        json.dumps({"characters": characters}, ensure_ascii=False), encoding="utf-8"
    )


def write_reference_metadata(tmp_path: Path) -> None:
    template_dir = tmp_path / "templates"
    template_dir.mkdir(exist_ok=True)
    (template_dir / "dps-table.json").write_text(
        json.dumps(
            {
                "source": {"width": 3974, "height": 14756, "observed_version": "V3.5.2"},
                "section_bands": [
                    {"id": "intro", "label": "说明", "content_y_px": [0, 199]},
                    {"id": "three", "label": "3.0", "content_y_px": [200, 400]},
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )


def write_portrait_assets(tmp_path: Path) -> bytes:
    portrait_dir = tmp_path / "portraits"
    portrait_dir.mkdir()
    original_bytes = b"lossless-cropped-png-bytes"
    (portrait_dir / "portrait-a.png").write_bytes(original_bytes)
    (tmp_path / "portrait-atlas.json").write_text(
        json.dumps(
            {
                "sources": [{"private_original_path": "C:/private/account-card.png"}],
                "portraits": [
                    {
                        "id": "portrait-a",
                        "canonical": "测试角色",
                        "aliases": ["测试别名"],
                        "image": "data/portraits/portrait-a.png",
                        "source_index": 0,
                        "card_bbox": {"x": 1},
                    }
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    return original_bytes


def test_health_and_explicit_demo_database_are_available(tmp_path: Path) -> None:
    with make_client(tmp_path, lambda *_: {"ok": True}) as client:
        assert client.get("/api/health").json() == {"status": "ok"}
        response = client.get("/api/database")
        assert response.status_code == 200
        assert response.json()["version"] == "test"
        assert response.headers["cache-control"] == "public, max-age=300"


def test_example_account_preserves_the_fixed_real_inventory_and_is_separate_from_demo(tmp_path: Path) -> None:
    source_data = Path(__file__).parents[2] / "data"
    example = json.loads((source_data / "example-account.json").read_text(encoding="utf-8"))
    (tmp_path / "example-account.json").write_text(json.dumps(example, ensure_ascii=False), encoding="utf-8")
    with make_client(tmp_path, lambda *_: {"ok": True}) as client:
        response = client.get("/api/example-account")
        assert response.status_code == 200
        assert response.json() == example
        assert response.headers["cache-control"] == "no-store"
        assert client.get("/api/example").json()["account"] == sample_account()

    from app.main import _normalize_api_account
    assets = _normalize_api_account(example, source_data)
    assert len(example["characters"]) == 25
    assert len(assets["weapons"]) == 14
    assert all(asset["refinement"] == 1 for asset in assets["weapons"])


def test_missing_example_account_does_not_substitute_demo_inventory(tmp_path: Path) -> None:
    with make_client(tmp_path, lambda *_: {"ok": True}) as client:
        response = client.get("/api/example-account")
        assert response.status_code == 503
        assert response.json()["detail"]["code"] == "public_data_unavailable"


@pytest.mark.parametrize("standard", ["维里奈", "安可", "卡卡罗", "凌阳", "鉴心"])
def test_team_gold_uses_each_row_configuration_and_excludes_standard_assets(standard):
    from app.main import _with_team_gold

    source_data = Path(__file__).parents[2] / "data"
    current_team = {"dps": 20, "members": [
        {"character": "今汐", "chain": 2, "weapon": "时和岁稔", "refinement": 1},
        {"character": standard, "chain": 6, "weapon": "千古洑流", "refinement": 5},
        {"character": "漂泊者·气动", "chain": 6, "weapon": "表内常驻·漂泊者·气动", "refinement": 1},
    ]}
    best_team = {"dps": 36, "members": [
        {"character": "今汐", "chain": 6, "weapon": "时和岁稔", "refinement": 5},
        *current_team["members"][1:],
    ]}
    result = _with_team_gold({"current": {"teams": [current_team]}, "best": {"teams": [best_team]},
                              "upgrade_path": [{"teams": [best_team]}]}, source_data)
    assert result["current"]["teams"][0]["gold_count"] == 4
    assert result["current"]["teams"][0]["dps_per_gold"] == 5
    assert result["best"]["teams"][0]["gold_count"] == 12
    assert result["best"]["teams"][0]["dps_per_gold"] == 3
    assert result["upgrade_path"][-1]["teams"] == result["best"]["teams"]
    assert "gold_count" not in current_team


def test_team_gold_counts_up_weapons_on_standard_characters_and_each_repeated_slot():
    from app.main import _with_team_gold

    source_data = Path(__file__).parents[2] / "data"
    team = {"dps": 20, "members": [
        {"character": "守岸人", "chain": 0, "weapon": "星序协响", "refinement": 1},
        {"character": "维里奈", "chain": 6, "weapon": "星序协响", "refinement": 1},
        {"character": "白芷", "chain": 6, "weapon": "星序协响", "refinement": 1},
    ]}
    result = _with_team_gold({"current": {"teams": [team]}}, source_data)
    assert result["current"]["teams"][0]["gold_count"] == 4
    assert result["current"]["teams"][0]["dps_per_gold"] == 5


@pytest.mark.parametrize("count_up", [True, False])
def test_team_gold_is_returned_for_current_and_best_and_handles_zero_denominator(tmp_path, count_up):
    current_team = {"dps": 100, "members": sample_database()["records"][0]["members"]}
    best_team = json.loads(json.dumps(current_team))
    best_team["members"][0]["chain"] = 2
    best_team["dps"] = 180
    with make_client(tmp_path, lambda *_: {"current": {"teams": [current_team]}, "best": {"teams": [best_team]}}) as client:
        write_signature_catalog(tmp_path, [{
            "character": "Jinhsi", "rarity": 5, "is_limited": count_up, "signature_weapon": "Test weapon",
        }])
        response = client.post("/api/optimize", json={"account": sample_account(), "settings": sample_settings()})
    assert response.status_code == 200
    result = response.json()
    assert "account_gold" not in result
    assert result["current"]["teams"][0]["gold_count"] == (4 if count_up else 0)
    assert result["current"]["teams"][0]["dps_per_gold"] == (25 if count_up else None)
    assert result["best"]["teams"][0]["gold_count"] == (6 if count_up else 0)
    assert result["best"]["teams"][0]["dps_per_gold"] == (30 if count_up else None)


@pytest.mark.parametrize("difficulty,expected_dps", [("中", 72.85), ("高", 75.86)])
def test_nine_gold_example_four_teams_completes_with_default_server_limits(difficulty, expected_dps):
    source_data = Path(__file__).parents[2] / "data"
    example = json.loads((source_data / "example-account.json").read_text(encoding="utf-8"))
    with TestClient(create_app(data_dir=source_data, config=RuntimeConfig(), use_process_pool=False)) as client:
        response = client.post("/api/optimize", json={
            "account": example,
            "settings": {"mode": "four_teams", "cost_mode": "gold", "budget": 9, "max_difficulty": difficulty},
        })
    assert response.status_code == 200
    result = response.json()
    assert result["exact"] is True
    assert result["explored_combinations"] < 250_000
    assert result["best"]["total_dps"] == expected_dps
    assert result["best"]["cost"] == 9
    assert len(result["best"]["teams"]) == 4
    assert len(result["upgrade_path"]) == 9
    assert result["upgrade_path"][-1]["total_dps"] == expected_dps
    assert result["upgrade_path"][-1]["teams"] == result["best"]["teams"]
    assert "account_gold" not in result
    for team in result["current"]["teams"] + result["best"]["teams"]:
        assert team["gold_count"] > 0
        assert team["dps_per_gold"] == pytest.approx(team["dps"] / team["gold_count"])


def test_default_database_and_optimize_use_reference_while_demo_is_separate(tmp_path: Path) -> None:
    reference_database = {**sample_database(), "version": "reference-v3.5.2"}
    demo_database = {**sample_database(), "version": "demo"}
    (tmp_path / "reference-dps-v3.5.2.json").write_text(json.dumps(reference_database), encoding="utf-8")
    (tmp_path / "demo-dps.json").write_text(json.dumps(demo_database), encoding="utf-8")
    received = {}

    def optimizer(account, database, settings):
        received["database"] = database
        return {"ok": True}

    with TestClient(
        create_app(
            data_dir=tmp_path,
            config=RuntimeConfig(optimizer_workers=1),
            optimizer=optimizer,
            use_process_pool=False,
        )
    ) as client:
        database = client.get("/api/database")
        demo = client.get("/api/demo-database")
        optimized = client.post("/api/optimize", json={"account": sample_account(), "settings": sample_settings()})

    assert database.status_code == 200
    assert database.json()["version"] == "reference-v3.5.2"
    assert demo.status_code == 200
    assert demo.json()["version"] == "demo"
    assert optimized.status_code == 200
    assert received["database"]["version"] == "reference-v3.5.2"


def test_missing_default_database_is_a_503_even_when_demo_exists(tmp_path: Path) -> None:
    (tmp_path / "demo-dps.json").write_text(json.dumps(sample_database()), encoding="utf-8")
    with TestClient(
        create_app(data_dir=tmp_path, config=RuntimeConfig(optimizer_workers=1), use_process_pool=False)
    ) as client:
        response = client.get("/api/database")
        demo = client.get("/api/demo-database")

    assert response.status_code == 503
    assert response.json()["detail"]["code"] == "public_data_unavailable"
    assert demo.status_code == 200


def test_database_filename_path_traversal_cannot_be_read(tmp_path: Path, monkeypatch) -> None:
    secret = tmp_path.parent / "private-database.json"
    secret.write_text(json.dumps({"secret": "must-not-leak"}), encoding="utf-8")
    monkeypatch.setenv("WUWA_DATABASE_FILE", "../private-database.json")

    with TestClient(create_app(data_dir=tmp_path, optimizer=lambda *_: {"ok": True}, use_process_pool=False)) as client:
        response = client.get("/api/database")

    assert RuntimeConfig.from_environment().database_filename == "reference-dps-v3.5.2.json"
    assert response.status_code == 503
    assert "must-not-leak" not in response.text


def test_simplified_account_gets_only_valid_declared_common_weapon_baseline(tmp_path: Path) -> None:
    write_signature_catalog(tmp_path, [{"character": "Jinhsi", "signature_weapon": "Ages of Harvest"}])
    received = {}

    def optimizer(account, database, settings):
        received["account"] = account
        return {"ok": True}

    database = {
        **sample_database(),
        "metadata": {
            "assumed_common_weapons": [
                {"character": "Jinhsi", "weapon": "表内常驻·Jinhsi", "refinement": 1}
            ]
        },
    }
    with make_client(tmp_path, optimizer) as client:
        response = client.post(
            "/api/optimize",
            json={
                "account": {"characters": [{"character": "Jinhsi", "chain": 0, "signature_refinement": 0}]},
                "settings": sample_settings(),
                "database": database,
            },
        )

    assert response.status_code == 200
    assert received["account"]["weapons"] == [
        {"id": f"assumed-common-{index}", "weapon": "表内常驻·Jinhsi", "refinement": 1}
        for index in range(1, 5)
    ]


def test_legacy_weapon_inventory_never_gets_common_weapon_baseline(tmp_path: Path) -> None:
    received = {}

    def optimizer(account, database, settings):
        received["account"] = account
        return {"ok": True}

    database = {
        **sample_database(),
        "metadata": {
            "assumed_common_weapons": [
                {"character": "Jinhsi", "weapon": "表内常驻·Jinhsi", "refinement": 1}
            ]
        },
    }
    with make_client(tmp_path, optimizer) as client:
        response = client.post(
            "/api/optimize",
            json={"account": sample_account(), "settings": sample_settings(), "database": database},
        )

    assert response.status_code == 200
    assert {weapon["id"] for weapon in received["account"]["weapons"]} == {
        "test-weapon-1",
        "test-weapon-2",
        "test-weapon-3",
    }


def test_client_database_cannot_grant_arbitrary_or_high_refinement_weapon(tmp_path: Path) -> None:
    write_signature_catalog(tmp_path, [{"character": "Jinhsi", "signature_weapon": "Ages of Harvest"}])
    database = {
        **sample_database(),
        "metadata": {
            "assumed_common_weapons": [
                {"character": "Jinhsi", "weapon": "Untrusted R5 weapon", "refinement": 5}
            ]
        },
    }
    with make_client(tmp_path, lambda *_: {"ok": True}) as client:
        response = client.post(
            "/api/optimize",
            json={
                "account": {"characters": [{"character": "Jinhsi", "chain": 0, "signature_refinement": 0}]},
                "settings": sample_settings(),
                "database": database,
            },
        )

    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "validation_error"


@pytest.mark.parametrize("rover", ["漂泊者·导电", "漂泊者·衍射", "漂泊者·气动"])
def test_non_gacha_forms_require_explicit_matching_chain_and_add_coverage_note(tmp_path: Path, rover: str) -> None:
    write_signature_catalog(tmp_path, [{"character": rover}])
    received = {}

    def optimizer(account, database, settings):
        received.update(account=account, database=database)
        return {"ok": True}

    database = {
        "version": "non-gacha-test",
        "metadata": {"non_gacha_characters": [rover]},
        "records": [
            {
                "id": "electro-rover-c6",
                "main_c": rover,
                "members": [
                    {"character": rover, "chain": 6, "weapon": "Test weapon", "refinement": 1},
                    {"character": "Test sub-DPS", "chain": 0, "weapon": "Test weapon", "refinement": 1},
                    {"character": "Test healer", "chain": 0, "weapon": "Test weapon", "refinement": 1},
                ],
                "rotations": [{"id": "normal", "difficulty": "中", "dps": 100}],
            }
        ],
    }
    with make_client(tmp_path, optimizer) as client:
        response = client.post(
            "/api/optimize",
            json={
                "account": {"characters": [{"character": rover, "chain": 5, "signature_refinement": 0}]},
                "settings": sample_settings(),
                "database": database,
            },
        )

    assert response.status_code == 200
    assert received["account"]["characters"][0] == {"character": rover, "chain": 5}
    assert received["database"]["records"] == []
    assert "不会自动赠送形态或共鸣链" in response.json()["database_coverage_note"]


def test_catalog_and_simplified_account_are_adapted_before_optimization(tmp_path: Path) -> None:
    write_signature_catalog(tmp_path, [{"character": "Jinhsi", "signature_weapon": "Ages of Harvest"}])
    received = {}

    def optimizer(account, database, settings):
        received.update(account=account, database=database, settings=settings)
        return {"ok": True}

    with make_client(tmp_path, optimizer) as client:
        catalog = client.get("/api/catalog")
        response = client.post(
            "/api/optimize",
            json={
                "account": {
                    "characters": [{"character": "Jinhsi", "chain": 2, "signature_refinement": 3}]
                },
                "settings": sample_settings(),
                "database": sample_database(),
            },
        )

    assert catalog.status_code == 200
    assert catalog.json()["characters"][0]["signature_weapon"] == "Ages of Harvest"
    assert catalog.json()["policy"] == {"assumed_four_star_chain": 6}
    assert response.status_code == 200
    assert received["account"]["characters"][0] == {"character": "Jinhsi", "chain": 2}
    assert received["account"]["weapons"] == [
        {"id": "signature-Jinhsi", "weapon": "Ages of Harvest", "refinement": 3}
    ]
    implied_four_stars = {
        entry["character"]: entry["chain"]
        for entry in received["account"]["characters"]
        if entry["character"] in {"秧秧", "白芷", "炽霞", "丹瑾", "莫特斐", "桃祈", "渊武", "散华", "秋水", "釉瑚", "灯灯", "卜灵"}
    }
    assert len(implied_four_stars) == 12
    assert set(implied_four_stars.values()) == {6}


def test_unknown_signature_weapon_is_a_validation_error(tmp_path: Path) -> None:
    write_signature_catalog(tmp_path, [{"character": "Jinhsi"}])
    with make_client(tmp_path, lambda *_: {"ok": True}) as client:
        response = client.post(
            "/api/optimize",
            json={
                "account": {
                    "characters": [{"character": "Jinhsi", "chain": 0, "signature_refinement": 1}]
                },
                "settings": sample_settings(),
                "database": sample_database(),
            },
        )

    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "validation_error"
    assert "cannot fabricate" in response.json()["detail"]["message"]


def test_exact_catalog_alias_canonicalizes_before_four_star_defaults(tmp_path: Path) -> None:
    write_signature_catalog(
        tmp_path,
        [{"character": "秧秧·玄翎", "signature_weapon": "天之苍苍", "aliases": ["玄翎", "秧秧玄翎"]}],
    )
    received = {}

    def optimizer(account, database, settings):
        received["account"] = account
        return {"ok": True}

    with make_client(tmp_path, optimizer) as client:
        response = client.post(
            "/api/optimize",
            json={
                "account": {
                    "characters": [{"character": "玄翎", "chain": 1, "signature_refinement": 1}]
                },
                "settings": sample_settings(),
                "database": sample_database(),
            },
        )

    assert response.status_code == 200
    characters = {entry["character"]: entry["chain"] for entry in received["account"]["characters"]}
    assert characters["秧秧·玄翎"] == 1
    assert characters["秧秧"] == 6
    assert "玄翎" not in characters
    assert received["account"]["weapons"] == [
        {"id": "signature-秧秧·玄翎", "weapon": "天之苍苍", "refinement": 1}
    ]


def test_alias_and_canonical_name_together_are_a_422(tmp_path: Path) -> None:
    write_signature_catalog(
        tmp_path,
        [{"character": "秧秧·玄翎", "signature_weapon": "天之苍苍", "aliases": ["玄翎"]}],
    )
    with make_client(tmp_path, lambda *_: {"ok": True}) as client:
        response = client.post(
            "/api/optimize",
            json={
                "account": {
                    "characters": [
                        {"character": "玄翎", "chain": 1, "signature_refinement": 1},
                        {"character": "秧秧·玄翎", "chain": 1, "signature_refinement": 1},
                    ]
                },
                "settings": sample_settings(),
                "database": sample_database(),
            },
        )

    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "validation_error"
    assert "alias normalization" in response.json()["detail"]["message"]


def test_unknown_custom_character_with_no_signature_refinement_can_pass(tmp_path: Path) -> None:
    write_signature_catalog(tmp_path, [{"character": "秧秧·玄翎", "signature_weapon": "天之苍苍", "aliases": ["玄翎"]}])
    received = {}

    def optimizer(account, database, settings):
        received["account"] = account
        return {"ok": True}

    with make_client(tmp_path, optimizer) as client:
        response = client.post(
            "/api/optimize",
            json={
                "account": {
                    "characters": [{"character": "Local custom name", "chain": 3, "signature_refinement": 0}]
                },
                "settings": sample_settings(),
                "database": sample_database(),
            },
        )

    assert response.status_code == 200
    assert received["account"]["characters"][0] == {"character": "Local custom name", "chain": 3}
    assert received["account"]["weapons"] == []


def test_reference_dps_metadata_and_image_are_public_exact_bytes(tmp_path: Path) -> None:
    write_reference_metadata(tmp_path)
    public_dir = tmp_path / "public"
    public_dir.mkdir()
    original_bytes = b"unmodified-public-jpeg-bytes"
    (public_dir / "reference-dps.jpg").write_bytes(original_bytes)

    with make_client(tmp_path, lambda *_: {"ok": True}) as client:
        metadata = client.get("/api/reference-dps")
        image = client.get("/api/reference-dps/image")

    assert metadata.status_code == 200
    assert metadata.json() == {
        "image_url": "/api/reference-dps/image",
        "width": 3974,
        "height": 14756,
        "version": "V3.5.2",
        "sections": [{"id": "intro", "label": "说明", "y": 0}, {"id": "three", "label": "3.0", "y": 200}],
    }
    assert image.status_code == 200
    assert image.content == original_bytes
    assert image.headers["content-type"] == "image/jpeg"
    assert image.headers["cache-control"] == "public, max-age=86400, immutable"


def test_reference_image_missing_is_a_truthful_service_error(tmp_path: Path) -> None:
    write_reference_metadata(tmp_path)
    with make_client(tmp_path, lambda *_: {"ok": True}) as client:
        response = client.get("/api/reference-dps/image")

    assert response.status_code == 503
    assert response.json()["detail"]["code"] == "reference_image_unavailable"


def test_dps_recognition_is_served_as_raw_documentary_json(tmp_path: Path) -> None:
    original_bytes = b'{"status":"draft","not_optimizer_input":true}'
    (tmp_path / "dps-recognition-v3.5.2.json").write_bytes(original_bytes)

    with make_client(tmp_path, lambda *_: {"ok": True}) as client:
        response = client.get("/api/dps-recognition")

    assert response.status_code == 200
    assert response.content == original_bytes
    assert response.headers["content-type"] == "application/json"
    assert response.headers["cache-control"] == "public, max-age=300"


def test_missing_dps_recognition_has_explicit_service_status(tmp_path: Path) -> None:
    with make_client(tmp_path, lambda *_: {"ok": True}) as client:
        response = client.get("/api/dps-recognition")

    assert response.status_code == 503
    assert response.json()["detail"]["code"] == "dps_recognition_unavailable"


def test_portrait_atlas_is_sanitized_and_png_is_whitelisted(tmp_path: Path) -> None:
    original_bytes = write_portrait_assets(tmp_path)
    with make_client(tmp_path, lambda *_: {"ok": True}) as client:
        atlas = client.get("/api/portrait-atlas")
        image = client.get("/api/portraits/portrait-a")
        unknown = client.get("/api/portraits/no-such-portrait")
        traversal = client.get("/api/portraits/%2E%2E%2Fportrait-atlas.json")

    assert atlas.status_code == 200
    assert atlas.json() == {
        "portraits": [
            {
                "id": "portrait-a",
                "canonical": "测试角色",
                "aliases": ["测试别名"],
                "image_url": "/api/portraits/portrait-a",
            }
        ]
    }
    assert "private_original_path" not in atlas.text
    assert "card_bbox" not in atlas.text
    assert image.status_code == 200
    assert image.content == original_bytes
    assert image.headers["content-type"] == "image/png"
    assert image.headers["cache-control"] == "public, max-age=300"
    assert unknown.status_code == 404
    assert traversal.status_code == 404


def test_optimize_injects_manual_database_and_server_caps_search_limit(tmp_path: Path) -> None:
    received = {}

    def optimizer(account, database, settings):
        received.update(account=account, database=database, settings=settings)
        return {"ok": True, "effective_limit": settings["search_limit"]}

    with make_client(tmp_path, optimizer, max_search_limit=9) as client:
        response = client.post(
            "/api/optimize",
            json={
                "account": sample_account(),
                "settings": {**sample_settings(), "search_limit": 99},
                "database": sample_database(),
            },
        )

    assert response.status_code == 200
    assert response.json() == {"ok": True, "effective_limit": 9}
    assert received["database"]["version"] == "test"


def test_request_size_and_shape_limits_are_rejected(tmp_path: Path) -> None:
    with make_client(tmp_path, lambda *_: {"ok": True}, max_records=1) as client:
        oversized = client.post(
            "/api/optimize",
            content=b"x" * (JSON_LIMIT_BYTES + 1),
            headers={"content-type": "application/json"},
        )
        too_many_records = client.post(
            "/api/optimize",
            json={
                "account": sample_account(),
                "settings": sample_settings(),
                "database": {"version": "test", "records": [{}, {}]},
            },
        )

    assert oversized.status_code == 413
    assert oversized.json()["detail"]["code"] == "payload_too_large"
    assert too_many_records.status_code == 422
    assert too_many_records.json()["detail"]["code"] == "too_many_records"


def test_card_recognition_endpoint_is_removed(tmp_path: Path) -> None:
    with make_client(tmp_path, lambda *_: {"ok": True}) as client:
        assert client.get("/api/parse").status_code == 404
        assert client.post("/api/parse", json={}).status_code == 404
        assert "/api/parse" not in client.get("/openapi.json").json()["paths"]


def test_api_defaults_all_four_stars_to_c6_for_legacy_inventory(tmp_path: Path) -> None:
    received = {}

    def optimizer(account, database, settings):
        received["account"] = account
        return {"ok": True}

    account = {"characters": [{"character": "秧秧", "chain": 0}], "weapons": []}
    with make_client(tmp_path, optimizer) as client:
        response = client.post(
            "/api/optimize",
            json={"account": account, "settings": sample_settings(), "database": sample_database()},
        )

    assert response.status_code == 200
    implied = {entry["character"]: entry["chain"] for entry in received["account"]["characters"]}
    assert len({name for name in implied if name in {"秧秧", "白芷", "炽霞", "丹瑾", "莫特斐", "桃祈", "渊武", "散华", "秋水", "釉瑚", "灯灯", "卜灵"}}) == 12
    assert implied["秧秧"] == 6


def test_api_uses_unlisted_four_star_c6_without_character_cost(tmp_path: Path) -> None:
    database = {
        "version": "four-star-test",
        "records": [
            {
                "id": "four-star-c6-team",
                "main_c": "秧秧",
                "members": [
                    {"character": "秧秧", "chain": 6, "weapon": "Test weapon", "refinement": 1},
                    {"character": "白芷", "chain": 6, "weapon": "Test weapon", "refinement": 1},
                    {"character": "散华", "chain": 6, "weapon": "Test weapon", "refinement": 1},
                ],
                "rotations": [{"id": "c6", "difficulty": "低", "dps": 777}],
            }
        ],
    }
    legacy_account = {
        "characters": [],
        "weapons": [
            {"id": "owned-1", "weapon": "Test weapon", "refinement": 1},
            {"id": "owned-2", "weapon": "Test weapon", "refinement": 1},
            {"id": "owned-3", "weapon": "Test weapon", "refinement": 1},
        ],
    }
    with TestClient(
        create_app(
            data_dir=tmp_path,
            config=RuntimeConfig(optimizer_workers=1, database_filename="demo-dps.json"),
            use_process_pool=False,
        )
    ) as client:
        response = client.post(
            "/api/optimize",
            json={"account": legacy_account, "settings": {**sample_settings(), "max_difficulty": "低"}, "database": database},
        )

    assert response.status_code == 200
    assert response.json()["current"]["total_dps"] == 777
    assert response.json()["best"]["cost"] == 0


def test_non_mapping_account_remains_a_422(tmp_path: Path) -> None:
    with make_client(tmp_path, lambda *_: {"ok": True}) as client:
        response = client.post(
            "/api/optimize",
            json={"account": [], "settings": sample_settings(), "database": sample_database()},
        )

    assert response.status_code == 422


def test_real_core_is_reachable_through_api(tmp_path: Path) -> None:
    source_data = Path(__file__).parents[2] / "data"
    for filename in ("demo-dps.json", "demo-request.json"):
        (tmp_path / filename).write_text((source_data / filename).read_text(encoding="utf-8"), encoding="utf-8")

    with TestClient(
        create_app(
            data_dir=tmp_path,
            config=RuntimeConfig(optimizer_workers=1, database_filename="demo-dps.json"),
            use_process_pool=False,
        )
    ) as client:
        request = client.get("/api/example").json()
        response = client.post("/api/optimize", json=request)

    assert response.status_code == 200
    assert response.json()["current"]["total_dps"] == 110000
    assert response.json()["best"]["total_dps"] == 165000


def test_process_pool_path_runs_on_windows(tmp_path: Path) -> None:
    source_data = Path(__file__).parents[2] / "data"
    for filename in ("demo-dps.json", "demo-request.json"):
        (tmp_path / filename).write_text((source_data / filename).read_text(encoding="utf-8"), encoding="utf-8")

    with TestClient(
        create_app(
            data_dir=tmp_path,
            config=RuntimeConfig(optimizer_workers=1, optimizer_timeout_seconds=5, database_filename="demo-dps.json"),
            use_process_pool=True,
        )
    ) as client:
        response = client.post("/api/optimize", json=client.get("/api/example").json())

    assert response.status_code == 200
    assert response.json()["best"]["total_dps"] == 165000


def test_core_validation_errors_are_stable_api_responses(tmp_path: Path) -> None:
    with TestClient(
        create_app(data_dir=tmp_path, config=RuntimeConfig(optimizer_workers=1), use_process_pool=False)
    ) as client:
        response = client.post(
            "/api/optimize",
            json={
                "account": sample_account(),
                "settings": {**sample_settings(), "search_limit": 1.5},
                "database": sample_database(),
            },
        )

    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "validation_error"
    assert "search_limit" in response.json()["detail"]["message"]


def test_busy_and_timed_out_searches_keep_capacity_reserved(tmp_path: Path) -> None:
    started = threading.Event()
    release = threading.Event()

    def slow_optimizer(*_):
        started.set()
        release.wait(timeout=5)
        return {"ok": True}

    app = create_app(
        data_dir=tmp_path,
        config=RuntimeConfig(optimizer_workers=1, optimizer_timeout_seconds=0.05),
        optimizer=slow_optimizer,
        use_process_pool=False,
    )
    with TestClient(app) as client, ThreadPoolExecutor(max_workers=1) as pool:
        timed_out = pool.submit(
            client.post,
            "/api/optimize",
            json={"account": sample_account(), "settings": sample_settings(), "database": sample_database()},
        )
        assert started.wait(timeout=1)
        assert timed_out.result(timeout=2).status_code == 504

        busy = client.post(
            "/api/optimize",
            json={"account": sample_account(), "settings": sample_settings(), "database": sample_database()},
        )
        assert busy.status_code == 429
        release.set()
        time.sleep(0.05)

        completed = client.post(
            "/api/optimize",
            json={"account": sample_account(), "settings": sample_settings(), "database": sample_database()},
        )

    assert completed.status_code == 200
