"""Adapter from the simplified character-card account shape to core assets.

The optimizer still needs independently addressable weapon instances because a
weapon cannot be equipped by two simultaneous teams.  The public account UI can
instead keep only one signature-weapon refinement alongside each character and
this adapter creates that one physical instance deterministically.
"""

from __future__ import annotations

from typing import Any

from .optimizer import ValidationError, validate_account


FOUR_STAR_CHARACTERS = (
    "秧秧",
    "白芷",
    "炽霞",
    "丹瑾",
    "莫特斐",
    "桃祈",
    "渊武",
    "散华",
    "秋水",
    "釉瑚",
    "灯灯",
    "卜灵",
)


def _fail(message: str) -> None:
    raise ValidationError(message)


def _text(value: Any, path: str) -> str:
    if not isinstance(value, str) or not value.strip():
        _fail(f"{path} must be a non-empty string")
    return value.strip()


def _integer(value: Any, path: str, minimum: int, maximum: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        _fail(f"{path} must be an integer")
    if value < minimum or value > maximum:
        _fail(f"{path} must be between {minimum} and {maximum}")
    return value


def _normalize_simplified_characters(account: dict[str, Any]) -> list[dict[str, Any]]:
    raw_characters = account.get("characters")
    if not isinstance(raw_characters, list):
        _fail("account.characters must be a list")

    characters: list[dict[str, Any]] = []
    names: set[str] = set()
    for index, raw_character in enumerate(raw_characters):
        path = f"account.characters[{index}]"
        if not isinstance(raw_character, dict):
            _fail(f"{path} must be an object")
        character = _text(raw_character.get("character"), f"{path}.character")
        if character in names:
            _fail(f"account.characters has duplicate character {character!r}")
        names.add(character)
        characters.append(
            {
                "character": character,
                "chain": _integer(raw_character.get("chain"), f"{path}.chain", 0, 6),
                "signature_refinement": _integer(
                    raw_character.get("signature_refinement"), f"{path}.signature_refinement", 0, 5
                ),
            }
        )
    return characters


def apply_default_four_stars(account: dict[str, Any]) -> dict[str, Any]:
    """Return a validated copy with every default four-star character at C6.

    The helper is opt-in because the standalone optimizer operates on explicit
    account JSON.  Callers for the public UI should invoke it before
    :func:`expand_character_account`.  New/simplified accounts receive C6 and
    no signature weapon (R0) for every default character.  Legacy accounts
    retain their supplied physical weapon inventory unchanged.
    """
    if not isinstance(account, dict):
        _fail("account must be an object")

    if "weapons" in account:
        # Reuse strict core validation before applying the policy.  It also
        # gives historic manual inventories the same canonical legacy shape.
        normalized = validate_account(account)
        by_name = {entry["character"]: dict(entry) for entry in normalized["characters"]}
        for character in FOUR_STAR_CHARACTERS:
            if character in by_name:
                by_name[character]["chain"] = 6
            else:
                by_name[character] = {"character": character, "chain": 6}
        # Preserve user order, then append implicit roster entries in the
        # stable roster order for deterministic JSON/UI behavior.
        existing_order = [entry["character"] for entry in normalized["characters"]]
        characters = [by_name[name] for name in existing_order]
        characters.extend(by_name[name] for name in FOUR_STAR_CHARACTERS if name not in existing_order)
        return {"characters": characters, "weapons": [dict(weapon) for weapon in normalized["weapons"]]}

    characters = _normalize_simplified_characters(account)
    by_name = {entry["character"]: dict(entry) for entry in characters}
    for character in FOUR_STAR_CHARACTERS:
        if character in by_name:
            # Four-stars use their generic C6 state and cannot contribute a
            # fabricated personal signature weapon in the simplified model.
            by_name[character]["chain"] = 6
            by_name[character]["signature_refinement"] = 0
        else:
            by_name[character] = {"character": character, "chain": 6, "signature_refinement": 0}
    existing_order = [entry["character"] for entry in characters]
    completed = [by_name[name] for name in existing_order]
    completed.extend(by_name[name] for name in FOUR_STAR_CHARACTERS if name not in existing_order)
    return {"characters": completed}


def expand_character_account(account: dict[str, Any], signature_catalog: dict[str, str]) -> dict[str, Any]:
    """Normalize a simplified account into the optimizer's legacy asset shape.

    The simplified shape has no ``weapons`` key::

        {"characters": [{"character": "Character", "chain": 0,
                         "signature_refinement": 1}]}

    Every non-zero signature refinement creates exactly one physical weapon
    instance named ``signature-<character>``.  Catalog lookup is exact after
    trimming the user-provided character name; aliases must therefore be added
    explicitly to the catalog.  A legacy account with a ``weapons`` key is
    retained as-is (after core validation), which preserves manual inventory
    imports without mixing it with the simplified representation.

    The input account and catalog are never mutated.
    """
    if not isinstance(account, dict):
        _fail("account must be an object")
    if not isinstance(signature_catalog, dict):
        _fail("signature_catalog must be an object")

    # Presence is the representation discriminator, including an explicitly
    # empty list.  The API can therefore preserve historic manual inventories.
    if "weapons" in account:
        return validate_account(account)

    normalized_characters = _normalize_simplified_characters(account)
    characters: list[dict[str, Any]] = []
    weapons: list[dict[str, Any]] = []
    for index, raw_character in enumerate(normalized_characters):
        character = raw_character["character"]
        chain = raw_character["chain"]
        refinement = raw_character["signature_refinement"]
        characters.append({"character": character, "chain": chain})
        if refinement == 0:
            continue

        weapon = signature_catalog.get(character)
        if not isinstance(weapon, str) or not weapon.strip():
            _fail(
                f"account.characters[{index}] has signature_refinement={refinement}, but signature_catalog "
                f"has no exact weapon name for {character!r}; cannot fabricate a signature weapon"
            )
        weapons.append(
            {
                "id": f"signature-{character}",
                "weapon": weapon.strip(),
                "refinement": refinement,
            }
        )

    # Reuse the optimizer's public validation so both representations arrive at
    # exactly the same canonical dictionary contract.
    return validate_account({"characters": characters, "weapons": weapons})
