"""Compile reviewed source labels into traceable, cumulative optimizer endpoints.

No OCR or guessed numbers enter this step. Source literals stay in the separate
transcription file. Unknown labels/difficulty and redundant upgrades fail closed.
"""
from __future__ import annotations

import argparse
from copy import deepcopy
from decimal import Decimal
import hashlib
import json
from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[1]
DIFFICULTY = {'低': '低', '中低': '中低', '中下': '中低', '中': '中',
              '中高': '中高', '中上': '中高', '高': '高'}
NUMERALS = str.maketrans('零一二三四五六', '0123456')
ALIASES = {'千咲': ('千', '干'), '尤诺': ('月',), '布兰特': ('船',),
           '长离': ('长', '离'), '相里要': ('相', '里')}
ROVERS = {'漂泊者·导电', '漂泊者·衍射', '漂泊者·气动'}


def common_weapon(character: str) -> str:
    # An equipment category, not a claim about a particular physical weapon.
    return f'表内常驻·{character}'


def resolve_alias(alias: str, names: list[str]) -> str:
    matches = [name for name in names if alias in ALIASES.get(name, ()) or
               name.startswith(alias) or (name == '秧秧·玄翎' and alias in ('秧', '玄', '羽'))]
    if len(matches) != 1:
        raise ValueError(f'Non-unique role abbreviation: {alias}')
    return matches[0]


def parse_label(label: str, names: list[str], last_character: str,
                unnamed_refinements: int) -> tuple[list[dict], str, int]:
    label = label.translate(NUMERALS).strip()
    main = names[0]
    if label == '全01':
        return ([{'character': name, 'chain': 0, 'signature_refinement': 1}
                 for name in names], main, unnamed_refinements)
    if label == '停驻精5':
        name = resolve_alias('莫', names)
        return ([{'character': name, 'common_refinement': 5}], name, unnamed_refinements)
    if re.fullmatch(r'[0-6]链', label):
        return ([{'character': main, 'chain': int(label[0])}], main, unnamed_refinements)
    if label == '专武':
        return ([{'character': main, 'signature_refinement': 1}], main, unnamed_refinements)
    if label == '5精':
        name = main if unnamed_refinements == 0 else last_character
        return ([{'character': name, 'signature_refinement': 5}], name, unnamed_refinements + 1)
    match = re.fullmatch(r'5精(.+)|(.+)5专|(.+)专', label)
    if match:
        alias = next(group for group in match.groups() if group)
        name = resolve_alias(alias, names)
        rank = 1 if match.group(3) else 5
        return ([{'character': name, 'signature_refinement': rank}], name, unnamed_refinements)
    match = re.fullmatch(r'([^0-9]+)([0-6])', label)
    if match:
        name = resolve_alias(match[1], names)
        return ([{'character': name, 'chain': int(match[2])}], name, unnamed_refinements)
    parts = list(re.finditer(r'([0-6]5|[0-6])([^0-9]+)', label))
    if not parts or ''.join(part[0] for part in parts) != label:
        raise ValueError(f'Unrecognized cumulative upgrade: {label}')
    operations = []
    for part in parts:
        digits, alias = part[1], part[2]
        name = resolve_alias(alias, names)
        operation = {'character': name, 'chain': int(digits[0])}
        if len(digits) == 2:
            operation['signature_refinement'] = 5
        operations.append(operation)
    return operations, operations[-1]['character'], unnamed_refinements


def compile_database(transcription: dict, catalog: dict) -> tuple[dict, dict]:
    entries = {entry['character']: entry for entry in catalog['characters']}
    records, excluded, tables_mapped, common_names = [], [], set(), set()
    manifest_tables = []
    for table in transcription['tables']:
        names = table['characters']
        difficulty = DIFFICULTY.get(table.get('difficulty'))
        if not difficulty:
            excluded.append({'table_id': table['id'], 'reason': '原图没有可确定的轴难度',
                             'row_count': len(table['rows'])})
            continue
        if any(name not in entries for name in names):
            raise ValueError(f"Role absent from catalog: {table['id']}")
        operations_by_row, last, unnamed = [], names[0], 0
        try:
            for row in table['rows']:
                operations, last, unnamed = parse_label(row[0], names, last, unnamed)
                operations_by_row.append(operations)
        except ValueError as error:
            excluded.append({'table_id': table['id'], 'reason': str(error),
                             'row_count': len(table['rows'])})
            continue
        # Default in the source legend: five stars C0/R1 signature, four stars
        # C6/R1 standard. An explicit R1 "change to signature" row overrides
        # that default for the preceding rows of that character.
        change_to_signature = {op['character'] for ops in operations_by_row for op in ops
                               if op.get('signature_refinement') == 1}
        if table['rows'][0][0] == '全01':
            change_to_signature = set()
        state = {}
        for name in names:
            entry = entries[name]
            generic = entry.get('rarity') == 4 or name in ROVERS or name in change_to_signature
            weapon = common_weapon(name) if generic else entry.get('signature_weapon')
            if not weapon:
                raise ValueError(f'Missing known signature weapon for {name}')
            if generic:
                common_names.add(name)
            state[name] = {'character': name, 'chain': 6 if entry.get('rarity') == 4 or name in ROVERS else 0,
                           'max_chain': 6, 'weapon': weapon, 'refinement': 1}
        table_manifest = {'table_id': table['id'], 'baseline': deepcopy(list(state.values())), 'rows': []}
        team_columns = [i for i, title in enumerate(table['headers']) if title in ('全队', '全队0层')]
        if len(team_columns) != 1:
            excluded.append({'table_id': table['id'], 'reason': '没有唯一的无条件全队列',
                             'row_count': len(table['rows'])})
            continue
        column = team_columns[0]
        previous_members = None
        table_records = []
        for index, (row, operations) in enumerate(zip(table['rows'], operations_by_row)):
            before = deepcopy(state)
            for op in operations:
                member = state[op['character']]
                if 'chain' in op:
                    member['chain'] = max(member['chain'], op['chain'])
                if 'signature_refinement' in op:
                    signature = entries[op['character']].get('signature_weapon')
                    if not signature:
                        raise ValueError(f"No signature for label {row[0]}")
                    member['refinement'] = max(member['refinement'], op['signature_refinement']) if member['weapon'] == signature else op['signature_refinement']
                    member['weapon'] = signature
                if 'common_refinement' in op:
                    member['weapon'] = common_weapon(op['character'])
                    member['refinement'] = op['common_refinement']
                    common_names.add(op['character'])
            members = deepcopy(list(state.values()))
            table_manifest['rows'].append({'row_index': index, 'label': row[0],
                                           'operations': operations, 'members': members})
            if previous_members == members:
                excluded.append({'table_id': table['id'], 'row_index': index, 'label': row[0],
                                 'reason': '累计配置没有变化，保留原文但不据此新增DPS端点', 'row_count': 1})
                continue
            previous_members = deepcopy(members)
            literal = row[column]
            if literal is None or not re.fullmatch(r'\d+(?:\.\d+)?', literal):
                excluded.append({'table_id': table['id'], 'row_index': index, 'label': row[0],
                                 'reason': '全队数值原文缺失或为星号，不插值', 'row_count': 1})
                continue
            dps = Decimal(literal)
            if dps <= 0:
                raise ValueError('Team DPS must be positive')
            # A known drop after a chain upgrade invalidates using the earlier
            # axis on an account that already crossed that chain boundary.
            if table_records and dps < Decimal(str(table_records[-1]['rotations'][0]['dps'])):
                changed = [name for name in names if state[name]['chain'] > before[name]['chain']]
                if len(changed) == 1:
                    name = changed[0]
                    for previous in table_records:
                        for member in previous['members']:
                            if member['character'] == name and member['chain'] < state[name]['chain']:
                                member['max_chain'] = min(member['max_chain'], state[name]['chain'] - 1)
            record_id = f"{table['id']}/row-{index + 1:02d}"
            table_records.append({'id': record_id, 'main_c': names[0], 'members': members,
                                  'rotations': [{'id': f"{table['title']} · {row[0]}",
                                                 'difficulty': difficulty, 'dps': float(dps)}],
                                  'source': {'table_id': table['id'], 'row_index': index,
                                             'label': row[0], 'title': table['title'],
                                             'headers': table['headers'], 'row': row,
                                             'team_column': table['headers'][column],
                                             'source_rect': table.get('source_rect'),
                                             'note': table.get('note', ''),
                                             'author': table.get('author'), 'source_text': table.get('source_text')}})
        records.extend(table_records)
        by_row = {record['source']['row_index']: record for record in table_records}
        for item in table_manifest['rows']:
            record = by_row.get(item['row_index'])
            item['optimizer_eligible'] = record is not None
            if record:
                item['members'] = deepcopy(record['members'])
        if table_records:
            tables_mapped.add(table['id'])
        manifest_tables.append(table_manifest)
    metadata = {'source': transcription['source'], 'unit': 'source_numeric',
                'unit_note': '沿用原图读数，原图未明确单位；不乘10000。条件表默认使用全队0层。',
                'source_table_count': len(transcription['tables']), 'source_row_count': transcription['summary']['row_count'],
                'mapped_table_count': len(tables_mapped), 'mapped_row_count': len(records),
                'mapped_table_ids': sorted(tables_mapped),
                'excluded_tables': excluded,
                'rules': {'cumulative_upgrades': True, 'unspecified_signature_refinement': 1,
                          'difficulty_aliases': {'中上': '中高', '中下': '中低'},
                          'character_chain_policy': '最低链数需求，已知链升级下降的轴另设上限',
                          'common_weapon_policy': '简化资产模式默认每队可配备精1常驻基线；每次分配使用独立实例，常驻具体名称未明时使用表内角色装备类别',
                          'conditional_team_column': '全队0层'},
                'assumed_common_weapons': [{'character': name, 'weapon': common_weapon(name), 'refinement': 1}
                                           for name in sorted(common_names)],
                'non_gacha_characters': sorted(ROVERS)}
    return ({'version': 'reference-v3.5.2-reviewed', 'metadata': metadata, 'records': records},
            {'source_sha256': transcription['source']['sha256'], 'user_confirmed_rules': metadata['rules'],
             'tables': manifest_tables, 'excluded': excluded})


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path, default=ROOT/'data/dps-transcription-v3.5.2.json')
    parser.add_argument('--catalog', type=Path, default=ROOT/'data/signature-weapons.json')
    parser.add_argument('--output', type=Path, default=ROOT/'data/reference-dps-v3.5.2.json')
    args = parser.parse_args()
    transcription = json.loads(args.source.read_text(encoding='utf-8'))
    source_hash = hashlib.sha256((ROOT/'data/public/reference-dps.jpg').read_bytes()).hexdigest()
    if transcription['source']['sha256'] != source_hash:
        raise ValueError('Transcription does not belong to the current source image.')
    database, manifest = compile_database(transcription,
                                          json.loads(args.catalog.read_text(encoding='utf-8')))
    from wuwa_optimizer import validate_database
    validate_database(database)
    args.output.write_text(json.dumps(database, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')
    (ROOT/'data/dps-configuration-mapping-v3.5.2.json').write_text(json.dumps(manifest, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')
    print(json.dumps({key: database['metadata'][key] for key in ('mapped_table_count', 'mapped_row_count')}, ensure_ascii=False))


if __name__ == '__main__':
    main()
