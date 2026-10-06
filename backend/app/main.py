"""Stateless public HTTP API.

The service deliberately stores no account data.  Optimisation runs in a small
process pool: CPU-heavy searches cannot block FastAPI's event loop, and a timed
out request never returns a partial answer.  A worker which has exceeded the
HTTP timeout keeps its slot until it actually exits, so the API reports 429
instead of silently queueing unbounded CPU work.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
from concurrent.futures import ProcessPoolExecutor
from contextlib import asynccontextmanager
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path, PurePosixPath
from typing import Any, Callable, Mapping

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, Response
from pydantic import BaseModel, ConfigDict


JSON_LIMIT_BYTES = 1_000_000
PORTRAIT_ID_PATTERN = re.compile(r"[a-z0-9][a-z0-9-]{0,63}")
DATABASE_FILENAME_PATTERN = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}\.json")
DEFAULT_DATABASE_FILENAME = "reference-dps-v3.5.2.json"
DEMO_DATABASE_FILENAME = "demo-dps.json"


try:  # Core is installed beside this package in deployment.
    from wuwa_optimizer import SearchLimitError, ValidationError
    from wuwa_optimizer.reference_adapter import NON_GACHA_CHARACTERS, prepare_reference_inputs
except ImportError:  # Lets API-only tooling import before the core package exists.
    class ValidationError(ValueError):
        """Fallback used only while the core package has not been installed."""

    class SearchLimitError(RuntimeError):
        """Fallback used only while the core package has not been installed."""

    NON_GACHA_CHARACTERS = frozenset({"漂泊者·导电", "漂泊者·衍射"})

    def prepare_reference_inputs(
        account: dict[str, Any], database: dict[str, Any], *, assume_common_weapons: bool
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        return dict(account), dict(database)


class OptimizePayload(BaseModel):
    """Untrusted client input; domain validation remains in ``wuwa_optimizer``."""

    model_config = ConfigDict(extra="forbid")

    account: dict[str, Any]
    settings: dict[str, Any]
    database: dict[str, Any] | None = None


@dataclass(frozen=True)
class RuntimeConfig:
    cors_origins: tuple[str, ...] = ("http://localhost:5173",)
    max_records: int = 5_000
    max_account_items: int = 256
    max_search_limit: int = 250_000
    optimizer_workers: int = 2
    optimizer_timeout_seconds: float = 20.0
    database_filename: str = DEFAULT_DATABASE_FILENAME

    @classmethod
    def from_environment(cls) -> "RuntimeConfig":
        origins = tuple(
            value.strip()
            for value in os.getenv("WUWA_CORS_ORIGINS", "http://localhost:5173").split(",")
            if value.strip()
        )
        return cls(
            cors_origins=origins,
            max_records=_positive_int_env("WUWA_MAX_RECORDS", 5_000, alias="OPTIMIZER_MAX_RECORDS"),
            max_account_items=_positive_int_env("WUWA_MAX_ACCOUNT_ITEMS", 256),
            max_search_limit=_positive_int_env("WUWA_MAX_SEARCH_LIMIT", 250_000, alias="OPTIMIZER_SEARCH_LIMIT"),
            optimizer_workers=_positive_int_env("WUWA_OPTIMIZER_WORKERS", 2, alias="OPTIMIZER_MAX_CONCURRENT"),
            optimizer_timeout_seconds=_positive_float_env(
                "WUWA_OPTIMIZER_TIMEOUT_SECONDS", 20.0, alias="OPTIMIZER_TIMEOUT_SECONDS"
            ),
            database_filename=_database_filename_env(),
        )


def _positive_int_env(name: str, default: int, *, alias: str | None = None) -> int:
    try:
        value = int(os.getenv(name, os.getenv(alias, str(default)) if alias else str(default)))
    except ValueError:
        return default
    return value if value > 0 else default


def _positive_float_env(name: str, default: float, *, alias: str | None = None) -> float:
    try:
        value = float(os.getenv(name, os.getenv(alias, str(default)) if alias else str(default)))
    except ValueError:
        return default
    return value if value > 0 else default


def _is_safe_database_filename(filename: object) -> bool:
    """Accept a plain JSON filename only; database paths are never configurable."""

    return isinstance(filename, str) and DATABASE_FILENAME_PATTERN.fullmatch(filename) is not None


def _database_filename_env() -> str:
    candidate = os.getenv("WUWA_DATABASE_FILE", DEFAULT_DATABASE_FILENAME)
    return candidate if _is_safe_database_filename(candidate) else DEFAULT_DATABASE_FILENAME


class RequestSizeLimitMiddleware:
    """Pre-buffer small API requests and fail oversized ones before parsing.

    The anonymous API accepts JSON only and never persists account data.
    """

    def __init__(self, app: Any) -> None:
        self.app = app

    async def __call__(self, scope: dict[str, Any], receive: Callable[..., Any], send: Callable[..., Any]) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        headers = {key.lower(): value for key, value in scope.get("headers", [])}
        limit = JSON_LIMIT_BYTES
        declared_size = headers.get(b"content-length")
        if declared_size:
            try:
                if int(declared_size) > limit:
                    await _send_json_error(send, 413, "payload_too_large", f"Request exceeds the {limit}-byte limit.")
                    return
            except ValueError:
                await _send_json_error(send, 400, "invalid_content_length", "Content-Length is not a valid integer.")
                return

        body = bytearray()
        more_body = True
        while more_body:
            message = await receive()
            if message["type"] == "http.disconnect":
                return
            chunk = message.get("body", b"")
            body.extend(chunk)
            if len(body) > limit:
                await _send_json_error(send, 413, "payload_too_large", f"Request exceeds the {limit}-byte limit.")
                return
            more_body = message.get("more_body", False)

        consumed = False

        async def replay() -> dict[str, Any]:
            nonlocal consumed
            if consumed:
                return {"type": "http.disconnect"}
            consumed = True
            return {"type": "http.request", "body": bytes(body), "more_body": False}

        await self.app(scope, replay, send)


async def _send_json_error(send: Callable[..., Any], status: int, code: str, message: str) -> None:
    payload = json.dumps({"detail": {"code": code, "message": message}}).encode("utf-8")
    await send(
        {
            "type": "http.response.start",
            "status": status,
            "headers": [
                (b"content-type", b"application/json"),
                (b"content-length", str(len(payload)).encode("ascii")),
            ],
        }
    )
    await send({"type": "http.response.body", "body": payload})


@lru_cache(maxsize=1)
def _resolve_optimizer() -> Callable[[dict[str, Any], dict[str, Any], dict[str, Any]], dict[str, Any]]:
    from wuwa_optimizer import optimize

    return optimize


def _run_optimizer_in_process(
    account: dict[str, Any], database: dict[str, Any], settings: dict[str, Any]
) -> dict[str, Any]:
    """Top-level and picklable for Windows' multiprocessing spawn method."""

    return _resolve_optimizer()(account, database, settings)


@lru_cache(maxsize=1)
def _resolve_account_normalizers() -> tuple[
    Callable[[dict[str, Any]], dict[str, Any]], Callable[[dict[str, Any], dict[str, str]], dict[str, Any]]
]:
    from wuwa_optimizer import apply_default_four_stars, expand_character_account

    return apply_default_four_stars, expand_character_account


def _project_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _default_data_dir() -> Path:
    configured = os.getenv("WUWA_DATA_DIR")
    return Path(configured) if configured else _project_root() / "data"


def _read_public_json(data_dir: Path, filename: str, *, max_records: int | None = None) -> dict[str, Any]:
    path = data_dir / filename
    if not path.is_file():
        raise HTTPException(
            status_code=503,
            detail={"code": "public_data_unavailable", "message": "Public data is not available yet."},
        )
    try:
        # Public data is always read afresh; handlers never mutate an in-memory copy.
        document = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise HTTPException(
            status_code=503,
            detail={"code": "public_data_invalid", "message": "Public data could not be read."},
        ) from exc
    if max_records is not None and isinstance(document, dict):
        records = document.get("records")
        if isinstance(records, list) and len(records) > max_records:
            raise HTTPException(
                status_code=503,
                detail={
                    "code": "public_data_too_large",
                    "message": "Public DPS data exceeds this server's configured record limit.",
                },
            )
    return document


def _read_database(data_dir: Path, filename: str, *, max_records: int) -> dict[str, Any]:
    """Read a configured public database without permitting path traversal."""

    if not _is_safe_database_filename(filename):
        raise HTTPException(
            status_code=503,
            detail={
                "code": "database_configuration_invalid",
                "message": "The configured public database filename is invalid.",
            },
        )
    return _read_public_json(data_dir, filename, max_records=max_records)


def _read_signature_catalog(
    data_dir: Path, *, allow_missing_for_legacy_inventory: bool
) -> tuple[dict[str, str], dict[str, str]]:
    """Return exact weapon and alias mappings from the public catalog.

    The public file may contain a character entry without a signature weapon.
    Such a character remains visible in ``/api/catalog`` but deliberately has
    no adapter mapping: the core then returns an explicit validation error
    instead of inventing a weapon name.

    Legacy inventories already name every physical weapon and remain usable in
    minimal test/development deployments where the optional public catalog has
    not been mounted.  That compatibility path uses identity-only naming.
    """

    path = data_dir / "signature-weapons.json"
    if not path.is_file() and allow_missing_for_legacy_inventory:
        return {}, {}
    document = _read_public_json(data_dir, "signature-weapons.json")
    entries = document.get("characters") if isinstance(document, dict) else None
    if not isinstance(entries, list):
        raise HTTPException(
            status_code=503,
            detail={"code": "public_catalog_invalid", "message": "Signature weapon catalog has an invalid characters list."},
        )

    weapons: dict[str, str] = {}
    exact_names: dict[str, str] = {}
    for index, entry in enumerate(entries):
        if not isinstance(entry, dict) or not isinstance(entry.get("character"), str) or not entry["character"].strip():
            raise HTTPException(
                status_code=503,
                detail={"code": "public_catalog_invalid", "message": f"Catalog entry {index} has no valid character name."},
            )
        character = entry["character"].strip()
        if character in exact_names:
            raise HTTPException(
                status_code=503,
                detail={"code": "public_catalog_invalid", "message": f"Catalog character {character} is duplicated."},
            )
        exact_names[character] = character

        aliases = entry.get("aliases", [])
        if not isinstance(aliases, list):
            raise HTTPException(
                status_code=503,
                detail={"code": "public_catalog_invalid", "message": f"Catalog aliases for {character} are invalid."},
            )
        for alias in aliases:
            if not isinstance(alias, str) or not alias.strip():
                raise HTTPException(
                    status_code=503,
                    detail={"code": "public_catalog_invalid", "message": f"Catalog alias for {character} is invalid."},
                )
            alias = alias.strip()
            existing = exact_names.get(alias)
            if existing is not None and existing != character:
                raise HTTPException(
                    status_code=503,
                    detail={"code": "public_catalog_invalid", "message": f"Catalog alias {alias} is ambiguous."},
                )
            exact_names[alias] = character

        weapon = entry.get("signature_weapon")
        if weapon is None:
            continue
        if not isinstance(weapon, str) or not weapon.strip() or character in weapons:
            raise HTTPException(
                status_code=503,
                detail={"code": "public_catalog_invalid", "message": f"Catalog entry for {character} is ambiguous or invalid."},
            )
        weapons[character] = weapon.strip()
    return weapons, exact_names


def _canonicalize_account_characters(account: dict[str, Any], exact_names: Mapping[str, str]) -> dict[str, Any]:
    """Canonicalize exact catalog aliases without applying fuzzy name matching."""

    raw_characters = account.get("characters")
    if not isinstance(raw_characters, list):
        return dict(account)

    characters: list[Any] = []
    seen: set[str] = set()
    for entry in raw_characters:
        if not isinstance(entry, dict) or not isinstance(entry.get("character"), str):
            characters.append(entry)
            continue
        supplied_name = entry["character"].strip()
        canonical_name = exact_names.get(supplied_name, supplied_name)
        if canonical_name in seen:
            raise ValidationError(f"account.characters contains duplicate character {canonical_name!r} after alias normalization")
        seen.add(canonical_name)
        normalized_entry = dict(entry)
        normalized_entry["character"] = canonical_name
        characters.append(normalized_entry)

    normalized_account = dict(account)
    normalized_account["characters"] = characters
    return normalized_account


def _normalize_api_account(account: dict[str, Any], data_dir: Path) -> dict[str, Any]:
    """Apply the public four-star policy, then normalize account inventory.

    The policy is deliberately applied to legacy and simplified payloads alike:
    all configured four-star characters are considered owned at C6.  No weapon
    is invented for them.  Full manual inventories stay valid, while the
    simplified shape creates a signature instance only when its refinement is
    non-zero.
    """

    try:
        apply_default_four_stars, expand_character_account = _resolve_account_normalizers()
        legacy_inventory = "weapons" in account
        signature_catalog, exact_names = _read_signature_catalog(
            data_dir, allow_missing_for_legacy_inventory=legacy_inventory
        )
        canonical_account = _canonicalize_account_characters(account, exact_names)
        defaulted_account = apply_default_four_stars(canonical_account)
        return expand_character_account(defaulted_account, signature_catalog)
    except HTTPException:
        raise
    except Exception as exc:
        raise _exception_response(exc) from exc


def _metadata_mapping(database: Mapping[str, Any]) -> Mapping[str, Any] | None:
    metadata = database.get("metadata")
    if metadata is None:
        return None
    if not isinstance(metadata, Mapping):
        raise ValidationError("database.metadata must be an object when provided")
    return metadata


def _validate_common_weapon_catalog(database: Mapping[str, Any], data_dir: Path) -> None:
    """Require every implicit baseline mapping to name a real catalog character."""

    metadata = _metadata_mapping(database)
    if metadata is None or "assumed_common_weapons" not in metadata:
        return
    entries = metadata["assumed_common_weapons"]
    if not isinstance(entries, list):
        return  # The shared adapter returns the detailed validation error.
    _, exact_names = _read_signature_catalog(data_dir, allow_missing_for_legacy_inventory=False)
    for index, entry in enumerate(entries):
        if not isinstance(entry, Mapping):
            return  # The shared adapter returns the detailed validation error.
        supplied_character = entry.get("character")
        if not isinstance(supplied_character, str) or not supplied_character.strip():
            return
        character = supplied_character.strip()
        if exact_names.get(character) != character:
            raise ValidationError(
                f"database.metadata.assumed_common_weapons[{index}].character must be an exact catalog character"
            )


def _reference_coverage_note(database: Mapping[str, Any]) -> str | None:
    metadata = _metadata_mapping(database)
    if metadata is None:
        return None
    names = metadata.get("non_gacha_characters")
    if not isinstance(names, list) or not any(name in NON_GACHA_CHARACTERS for name in names):
        return None
    return "漂泊者·导电、漂泊者·衍射等非抽卡形态仅在账号中手动录入对应形态且共鸣链达到表格要求时纳入；不会自动赠送形态或共鸣链。"


def _prepare_database_for_account(
    raw_account: Mapping[str, Any], account: dict[str, Any], database: dict[str, Any], data_dir: Path
) -> tuple[dict[str, Any], dict[str, Any], str | None]:
    """Run the shared reference adapter plus API-only catalog validation."""

    if not isinstance(database, dict):
        raise ValidationError("database must be an object")
    assume_common_weapons = "weapons" not in raw_account
    if assume_common_weapons:
        _validate_common_weapon_catalog(database, data_dir)
    prepared_account, prepared_database = prepare_reference_inputs(
        account, database, assume_common_weapons=assume_common_weapons
    )
    return prepared_account, prepared_database, _reference_coverage_note(database)


def _reference_dps_payload(data_dir: Path) -> dict[str, Any]:
    """Expose navigation metadata for the original public DPS JPEG only."""

    document = _read_public_json(data_dir / "templates", "dps-table.json")
    source = document.get("source") if isinstance(document, dict) else None
    section_bands = document.get("section_bands") if isinstance(document, dict) else None
    if not isinstance(source, dict) or not isinstance(section_bands, list):
        raise HTTPException(
            status_code=503,
            detail={"code": "reference_metadata_invalid", "message": "Reference DPS metadata is invalid."},
        )
    width = source.get("width")
    height = source.get("height")
    version = source.get("observed_version")
    if (
        isinstance(width, bool)
        or not isinstance(width, int)
        or isinstance(height, bool)
        or not isinstance(height, int)
        or not isinstance(version, str)
        or not version.strip()
    ):
        raise HTTPException(
            status_code=503,
            detail={"code": "reference_metadata_invalid", "message": "Reference DPS dimensions or version are invalid."},
        )
    sections: list[dict[str, Any]] = []
    for index, section in enumerate(section_bands):
        if not isinstance(section, dict):
            raise HTTPException(
                status_code=503,
                detail={"code": "reference_metadata_invalid", "message": f"Reference section {index} is invalid."},
            )
        y_values = section.get("content_y_px")
        section_id = section.get("id")
        label = section.get("label")
        if (
            not isinstance(section_id, str)
            or not isinstance(label, str)
            or not isinstance(y_values, list)
            or not y_values
            or isinstance(y_values[0], bool)
            or not isinstance(y_values[0], int)
        ):
            raise HTTPException(
                status_code=503,
                detail={"code": "reference_metadata_invalid", "message": f"Reference section {index} is invalid."},
            )
        sections.append({"id": section_id, "label": label, "y": y_values[0]})
    return {
        "image_url": "/api/reference-dps/image",
        "width": width,
        "height": height,
        "version": version,
        "sections": sections,
    }


def _recognition_result_path(data_dir: Path) -> Path:
    """Return the fixed documentary OCR artifact without parsing it."""

    path = data_dir / "dps-recognition-v3.5.2.json"
    if not path.is_file():
        raise HTTPException(
            status_code=503,
            detail={
                "code": "dps_recognition_unavailable",
                "message": "Documentary DPS recognition results are not available yet.",
            },
        )
    return path


def _portrait_asset_path(data_dir: Path, image_reference: Any, portrait_id: str) -> Path:
    """Resolve only a manifest-approved PNG below the configured data root."""

    if not isinstance(image_reference, str):
        raise HTTPException(
            status_code=503,
            detail={"code": "portrait_atlas_invalid", "message": f"Portrait {portrait_id} has no image path."},
        )
    relative = PurePosixPath(image_reference)
    if (
        relative.is_absolute()
        or relative.parts[:2] != ("data", "portraits")
        or len(relative.parts) != 3
        or relative.name != f"{portrait_id}.png"
        or relative.suffix.lower() != ".png"
    ):
        raise HTTPException(
            status_code=503,
            detail={"code": "portrait_atlas_invalid", "message": f"Portrait {portrait_id} has an unsafe image path."},
        )

    asset_root = (data_dir / "portraits").resolve()
    candidate = (data_dir / Path(*relative.parts[1:])).resolve()
    if not candidate.is_relative_to(asset_root):
        raise HTTPException(
            status_code=503,
            detail={"code": "portrait_atlas_invalid", "message": f"Portrait {portrait_id} resolves outside the asset directory."},
        )
    return candidate


def _portrait_manifest(data_dir: Path) -> dict[str, tuple[dict[str, Any], Path]]:
    """Read the small atlas and strip every non-public field at the boundary."""

    document = _read_public_json(data_dir, "portrait-atlas.json")
    portraits = document.get("portraits") if isinstance(document, dict) else None
    if not isinstance(portraits, list):
        raise HTTPException(
            status_code=503,
            detail={"code": "portrait_atlas_invalid", "message": "Portrait atlas has an invalid portraits list."},
        )

    manifest: dict[str, tuple[dict[str, Any], Path]] = {}
    for index, entry in enumerate(portraits):
        if not isinstance(entry, dict):
            raise HTTPException(
                status_code=503,
                detail={"code": "portrait_atlas_invalid", "message": f"Portrait atlas entry {index} is invalid."},
            )
        portrait_id = entry.get("id")
        canonical = entry.get("canonical")
        aliases = entry.get("aliases")
        if (
            not isinstance(portrait_id, str)
            or PORTRAIT_ID_PATTERN.fullmatch(portrait_id) is None
            or not isinstance(canonical, str)
            or not canonical.strip()
            or not isinstance(aliases, list)
            or any(not isinstance(alias, str) or not alias.strip() for alias in aliases)
            or portrait_id in manifest
        ):
            raise HTTPException(
                status_code=503,
                detail={"code": "portrait_atlas_invalid", "message": f"Portrait atlas entry {index} is invalid."},
            )
        asset_path = _portrait_asset_path(data_dir, entry.get("image"), portrait_id)
        manifest[portrait_id] = (
            {
                "id": portrait_id,
                "canonical": canonical.strip(),
                "aliases": [alias.strip() for alias in aliases],
                "image_url": f"/api/portraits/{portrait_id}",
            },
            asset_path,
        )
    return manifest


def _limited_settings(settings: Mapping[str, Any], config: RuntimeConfig) -> dict[str, Any]:
    effective = dict(settings)
    requested = effective.get("search_limit", config.max_search_limit)
    # Never coerce a float or string (for example 1.9) into an integer.  The
    # core validator should return its normal detailed 422 response for it.
    if isinstance(requested, int) and not isinstance(requested, bool):
        effective["search_limit"] = min(requested, config.max_search_limit)
    return effective


def _enforce_shape_limits(payload: OptimizePayload, config: RuntimeConfig) -> None:
    database = payload.database
    if database is not None:
        records = database.get("records")
        if isinstance(records, list) and len(records) > config.max_records:
            raise HTTPException(
                status_code=422,
                detail={
                    "code": "too_many_records",
                    "message": f"At most {config.max_records} DPS records are accepted per request.",
                },
            )
    for collection_name in ("characters", "weapons"):
        collection = payload.account.get(collection_name)
        if isinstance(collection, list) and len(collection) > config.max_account_items:
            raise HTTPException(
                status_code=422,
                detail={
                    "code": "too_many_account_items",
                    "message": f"At most {config.max_account_items} {collection_name} are accepted per request.",
                },
            )


def _exception_response(error: Exception) -> HTTPException:
    if isinstance(error, ValidationError):
        return HTTPException(
            status_code=422,
            detail={"code": "validation_error", "message": str(error) or "Input validation failed."},
        )
    if isinstance(error, SearchLimitError):
        return HTTPException(
            status_code=422,
            detail={
                "code": "search_limit_reached",
                "message": str(error) or "Search limit reached; no partial recommendation was returned.",
            },
        )
    return HTTPException(
        status_code=500,
        detail={"code": "optimizer_failed", "message": "The optimizer could not complete this request."},
    )


def create_app(
    *,
    data_dir: Path | None = None,
    config: RuntimeConfig | None = None,
    optimizer: Callable[[dict[str, Any], dict[str, Any], dict[str, Any]], dict[str, Any]] | None = None,
    use_process_pool: bool = True,
) -> FastAPI:
    """Create an API application.

    ``optimizer`` and ``use_process_pool=False`` are test seams. Production uses
    a bounded process pool because optimisation is CPU-bound.
    """

    runtime_config = config or RuntimeConfig.from_environment()
    public_data_dir = data_dir or _default_data_dir()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        app.state.optimizer_slots = asyncio.BoundedSemaphore(runtime_config.optimizer_workers)
        app.state.executor = ProcessPoolExecutor(max_workers=runtime_config.optimizer_workers) if use_process_pool else None
        app.state.optimizer = optimizer or _resolve_optimizer()
        try:
            yield
        finally:
            if app.state.executor is not None:
                app.state.executor.shutdown(wait=False, cancel_futures=True)

    application = FastAPI(title="Wuwa Pull Planner API", version="0.1.0", lifespan=lifespan)
    application.add_middleware(RequestSizeLimitMiddleware)
    application.add_middleware(
        CORSMiddleware,
        allow_origins=list(runtime_config.cors_origins),
        allow_credentials=False,
        allow_methods=["GET", "POST"],
        allow_headers=["Content-Type"],
    )

    @application.get("/api/health")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    @application.get("/api/database")
    async def public_database() -> Response:
        return JSONResponse(
            _read_database(
                public_data_dir, runtime_config.database_filename, max_records=runtime_config.max_records
            ),
            headers={"Cache-Control": "public, max-age=300"},
        )

    @application.get("/api/demo-database")
    async def demo_database() -> Response:
        return JSONResponse(
            _read_database(public_data_dir, DEMO_DATABASE_FILENAME, max_records=runtime_config.max_records),
            headers={"Cache-Control": "public, max-age=300"},
        )

    @application.get("/api/example")
    async def public_example() -> Response:
        filename = "demo-character-request.json" if (public_data_dir / "demo-character-request.json").is_file() else "demo-request.json"
        return JSONResponse(
            _read_public_json(public_data_dir, filename),
            headers={"Cache-Control": "public, max-age=300"},
        )

    @application.get("/api/catalog")
    async def public_catalog() -> Response:
        catalog = _read_public_json(public_data_dir, "signature-weapons.json")
        if not isinstance(catalog, dict):
            raise HTTPException(
                status_code=503,
                detail={"code": "public_catalog_invalid", "message": "Public signature catalog is invalid."},
            )
        return JSONResponse(
            {**catalog, "policy": {"assumed_four_star_chain": 6}},
            headers={"Cache-Control": "public, max-age=300"},
        )

    @application.get("/api/reference-dps")
    async def reference_dps() -> dict[str, Any]:
        return _reference_dps_payload(public_data_dir)

    @application.get("/api/reference-dps/image")
    async def reference_dps_image() -> Response:
        image_path = public_data_dir / "public" / "reference-dps.jpg"
        if not image_path.is_file():
            raise HTTPException(
                status_code=503,
                detail={"code": "reference_image_unavailable", "message": "Reference DPS image is not available yet."},
            )
        return FileResponse(
            image_path,
            media_type="image/jpeg",
            headers={"Cache-Control": "public, max-age=86400, immutable", "X-Content-Type-Options": "nosniff"},
        )

    @application.get("/api/dps-recognition")
    async def dps_recognition() -> Response:
        """Serve the large draft artifact as-is; it is not optimizer input."""

        return FileResponse(
            _recognition_result_path(public_data_dir),
            media_type="application/json",
            headers={"Cache-Control": "public, max-age=300", "X-Content-Type-Options": "nosniff"},
        )

    @application.get("/api/portrait-atlas")
    async def portrait_atlas() -> Response:
        manifest = _portrait_manifest(public_data_dir)
        return JSONResponse(
            {"portraits": [record for record, _ in manifest.values()]},
            headers={"Cache-Control": "public, max-age=300"},
        )

    @application.get("/api/portraits/{portrait_id}")
    async def portrait_image(portrait_id: str) -> Response:
        if PORTRAIT_ID_PATTERN.fullmatch(portrait_id) is None:
            raise HTTPException(status_code=404, detail={"code": "portrait_not_found", "message": "Portrait not found."})
        portrait = _portrait_manifest(public_data_dir).get(portrait_id)
        if portrait is None:
            raise HTTPException(status_code=404, detail={"code": "portrait_not_found", "message": "Portrait not found."})
        _, asset_path = portrait
        if not asset_path.is_file():
            raise HTTPException(
                status_code=503,
                detail={"code": "portrait_asset_unavailable", "message": "Portrait asset is not available yet."},
            )
        return FileResponse(
            asset_path,
            media_type="image/png",
            headers={"Cache-Control": "public, max-age=300", "X-Content-Type-Options": "nosniff"},
        )

    @application.post("/api/optimize")
    async def optimize_endpoint(payload: OptimizePayload) -> dict[str, Any]:
        _enforce_shape_limits(payload, runtime_config)
        account = _normalize_api_account(payload.account, public_data_dir)
        database = (
            payload.database
            if payload.database is not None
            else _read_database(
                public_data_dir, runtime_config.database_filename, max_records=runtime_config.max_records
            )
        )
        try:
            account, database, database_coverage_note = _prepare_database_for_account(
                payload.account, account, database, public_data_dir
            )
        except HTTPException:
            raise
        except Exception as exc:
            raise _exception_response(exc) from exc
        settings = _limited_settings(payload.settings, runtime_config)

        slots: asyncio.BoundedSemaphore = application.state.optimizer_slots
        if slots.locked():
            raise HTTPException(
                status_code=429,
                detail={"code": "optimizer_busy", "message": "Optimizer capacity is busy. Please retry shortly."},
                headers={"Retry-After": "3"},
            )
        await slots.acquire()
        slot_transferred_to_future = False
        future: asyncio.Future[dict[str, Any]] | None = None
        try:
            if application.state.executor is not None:
                loop = asyncio.get_running_loop()
                future = loop.run_in_executor(
                    application.state.executor,
                    _run_optimizer_in_process,
                    account,
                    database,
                    settings,
                )
            else:
                future = asyncio.create_task(asyncio.to_thread(application.state.optimizer, account, database, settings))

            def release_after_completion(completed: asyncio.Future[dict[str, Any]]) -> None:
                # Observe any late exception.  A timeout/client disconnect must
                # not produce an unhandled-future warning or open CPU capacity
                # before the worker really exits.
                try:
                    completed.exception()
                except asyncio.CancelledError:
                    pass
                finally:
                    slots.release()

            future.add_done_callback(release_after_completion)
            slot_transferred_to_future = True

            try:
                result = await asyncio.wait_for(asyncio.shield(future), timeout=runtime_config.optimizer_timeout_seconds)
                if database_coverage_note is not None:
                    result = {**result, "database_coverage_note": database_coverage_note}
                return result
            except TimeoutError as exc:
                raise HTTPException(
                    status_code=504,
                    detail={
                        "code": "optimizer_timeout",
                        "message": "Optimization timed out; no partial recommendation was returned.",
                    },
                ) from exc
            except Exception as exc:  # Domain errors are converted to stable public responses.
                raise _exception_response(exc) from exc
        finally:
            # If the client disconnects before a future is scheduled, return
            # the slot here.  Once scheduled, its callback owns the slot.
            if not slot_transferred_to_future:
                slots.release()

    return application


app = create_app()
