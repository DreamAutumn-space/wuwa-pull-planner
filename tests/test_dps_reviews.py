from copy import deepcopy

import pytest

from scripts.apply_dps_reviews import apply_reviews, merge_reviews


def test_review_preserves_raw_evidence_and_cannot_promote_a_sample_to_optimizer_data():
    payload = {
        'source': {'sha256': 'source-a'},
        'raw_ocr': {'tokens': [{'text': '0链 | 13.02', 'confidence': 0.92}]},
        'panels': [{'id': 'sample', 'characters': [{'name': None, 'unknown': True, 'score': 0.2}], 'optimizer_eligible': False}],
        'summary': {},
    }
    original = deepcopy(payload)
    review = {'source_sha256': 'source-a', 'unit': None, 'panels': [{'id': 'sample', 'characters': ['秧秧·玄翎'], 'tables': []}]}
    result = apply_reviews(payload, review)
    assert payload == original
    assert result['raw_ocr'] == original['raw_ocr']
    assert result['panels'][0]['characters'][0]['automatic_name'] is None
    assert result['panels'][0]['characters'][0]['name'] == '秧秧·玄翎'
    assert result['panels'][0]['optimizer_eligible'] is False
    assert result['summary']['manually_checked_avatar_slots'] == 1


def test_review_refuses_a_different_source_image():
    with pytest.raises(ValueError, match='different source image'):
        apply_reviews({'source': {'sha256': 'other'}}, {'source_sha256': 'expected'})


def test_sparse_review_does_not_claim_other_slots_are_verified():
    payload = {'source': {'sha256': 'a'}, 'panels': [{'id': 'p', 'characters': [
        {'name': None, 'unknown': True}, {'name': '菲比', 'unknown': False}
    ], 'optimizer_eligible': False}], 'summary': {}}
    result = apply_reviews(payload, {'source_sha256': 'a', 'panels': [
        {'id': 'p', 'characters': ['赞妮', None], 'tables': []}
    ]})
    assert result['summary']['manually_checked_avatar_slots'] == 1
    assert 'identity_verification' not in result['panels'][0]['characters'][1]


def test_merging_numeric_review_preserves_confirmed_roles_and_sample_tables():
    base = {'source_sha256': 'a', 'panels': [
        {'id': 'p', 'characters': ['绯雪', None, '穗穗'], 'tables': [{'title': 'old'}]}]}
    roles = {'source_sha256': 'a', 'panels': [
        {'id': 'p', 'characters': [None, '琳奈', None], 'tables': []}]}
    result = merge_reviews(base, roles)
    assert result['panels'][0]['characters'] == ['绯雪', '琳奈', '穗穗']
    assert result['panels'][0]['tables'] == [{'title': 'old'}]
    assert base['panels'][0]['characters'][1] is None
    with pytest.raises(ValueError, match='different source'):
        merge_reviews(base, {'source_sha256': 'b'})


def test_review_statistics_do_not_promote_verified_numbers_to_optimizer_input():
    payload = {'source': {'sha256': 'a'}, 'panels': [{'id': 'p', 'characters': [
        {'name': '坎特蕾拉', 'unknown': False}], 'tables': [{}]}], 'summary': {}}
    review = {'source_sha256': 'a', 'panels': [{'id': 'p', 'characters': ['绯雪'],
        'tables': [{'numeric_verification': 'complete_visual_transcription', 'rows': [['0链', '10.72']]}]}]}
    result = apply_reviews(payload, review)
    assert result['panels'][0]['characters'][0]['automatic_name'] == '坎特蕾拉'
    assert result['summary']['visually_transcribed_table_count'] == 1
    assert result['summary']['visually_transcribed_row_count'] == 1
    assert result['panels'][0]['optimizer_eligible'] is False
