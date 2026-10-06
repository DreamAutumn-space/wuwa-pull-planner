"""JSON-only entry point: python scripts/optimize.py --request ... --database ..."""
import argparse
from copy import deepcopy
import json
import sys
from pathlib import Path

from wuwa_optimizer import SearchLimitError, ValidationError, apply_default_four_stars, expand_character_account, optimize
from wuwa_optimizer.reference_adapter import prepare_reference_inputs


def main() -> int:
    parser = argparse.ArgumentParser(description="鸣潮 DPS 优化器（期望抽数 / 补金数量预算）")
    parser.add_argument("--request", type=Path, default=Path("data/reference-request.json"))
    parser.add_argument("--database", type=Path, default=Path("data/reference-dps-v3.5.2.json"))
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    try:
        request = json.loads(args.request.read_text(encoding="utf-8-sig"))
        database = json.loads(args.database.read_text(encoding="utf-8-sig"))
        account = deepcopy(request['account'])
        if not isinstance(account, dict):
            raise ValidationError('account must be an object')
        catalog_path = Path(__file__).resolve().parents[1] / 'data/signature-weapons.json'
        catalog = json.loads(catalog_path.read_text(encoding='utf-8'))['characters'] if catalog_path.is_file() else []
        names = {label: entry['character'] for entry in catalog for label in [entry['character'], *entry.get('aliases', [])]}
        for entry in account.get('characters', []):
            if isinstance(entry, dict) and isinstance(entry.get('character'), str):
                entry['character'] = names.get(entry['character'].strip(), entry['character'])
        signatures = {entry['character']: entry['signature_weapon'] for entry in catalog if entry.get('signature_weapon')}
        simplified = 'weapons' not in account
        assets = expand_character_account(apply_default_four_stars(account), signatures)
        assets, database = prepare_reference_inputs(assets, database, assume_common_weapons=simplified)
        result = optimize(assets, database, request["settings"])
        serialized = json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False)
        if args.output:
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(serialized + "\n", encoding="utf-8")
        else:
            print(serialized)
        return 0
    except (OSError, KeyError, TypeError, ValueError, ValidationError, SearchLimitError) as exc:
        print(f"输入或搜索失败：{exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
