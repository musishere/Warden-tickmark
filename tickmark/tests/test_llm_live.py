"""One real LLM call: does it map the unfamiliar sheet from headers + value patterns alone?
Opt-in (pytest -m llm) and skipped unless TICKMARK_LLM_* are set in .env. Costs one small request."""
from pathlib import Path

import pytest

from tickmark.config import settings
from tickmark.definitions import INVOICE_VOUCHING
from tickmark.fields.llm_mapping import LLMProposer, to_mapping
from tickmark.fields.mapping import validate
from tickmark.fields.sheet import read_sheet
from tools.datagen.unfamiliar import EXPECTED, write_unfamiliar

pytestmark = pytest.mark.llm
GEN = Path(__file__).resolve().parents[1] / "data/generated"


@pytest.mark.skipif(not (settings.llm_base_url and settings.llm_api_key and settings.llm_mapping_model),
                    reason="TICKMARK_LLM_BASE_URL / _API_KEY / _MAPPING_MODEL not set")
def test_llm_maps_unfamiliar_headers_without_values(tmp_path):
    write_unfamiliar(GEN / "sample.xlsx", tmp_path)
    sheet = read_sheet(tmp_path / "sample.xlsx")
    proposal = LLMProposer(send_values=False).propose(sheet, INVOICE_VOUCHING)
    assert proposal is not None and proposal.sent_values is False
    assert {f: c for f, c in proposal.columns.items() if f in EXPECTED} == EXPECTED
    assert validate(sheet, INVOICE_VOUCHING, to_mapping(sheet, INVOICE_VOUCHING, proposal)) == []
