from copy import deepcopy
import pytest
from scripts.export_dps_transcription import export_transcription


def fixture():
    payload = {'source': {'sha256': 'a'}, 'panels': [{'id': 'p', 'section_id': '3.0',
        'characters': [{'name': '绯雪'}]}]}
    review = {'source_sha256': 'a', 'panels': [{'id': 'p', 'tables': [{
        'numeric_verification': 'complete_visual_transcription',
        'headers': ['配置','全队','提升'], 'rows': [['0链','10.72',''], ['1链','12.10','***']]}]}]}
    return payload, review


def test_transcription_preserves_source_strings_blanks_and_does_not_claim_optimizer_ready():
    payload, review = fixture()
    before = deepcopy(review)
    result = export_transcription(payload, review)
    assert result['tables'][0]['rows'] == before['panels'][0]['tables'][0]['rows']
    assert result['summary'] == {'table_count': 1, 'row_count': 2}
    assert result['tables'][0]['characters'] == ['绯雪']
    assert result['tables'][0]['optimizer_eligible'] is False
    assert review == before


def test_transcription_refuses_stale_source_or_misaligned_columns():
    payload, review = fixture()
    review['source_sha256'] = 'other'
    with pytest.raises(ValueError, match='different source'):
        export_transcription(payload, review)
    review['source_sha256'] = 'a'
    review['panels'][0]['tables'][0]['rows'][0].pop()
    with pytest.raises(ValueError, match='Column count'):
        export_transcription(payload, review)
