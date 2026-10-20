"""One real Claude call: does it map the unfamiliar sheet from headers + value patterns alone?
Opt-in (pytest -m llm) and skipped without Anthropic credentials. Costs one small request."""
import os
from pathlib import Path

import pytest

from tickmark.definitions import INVOICE_VOUCHING
from tickmark.fields.llm_mapping import ClaudeProposer, to_mapping
from tickmark.fields.mapping import validate
from tickmark.fields.sheet import read_sheet
from tools.datagen.unfamiliar import EXPECTED, write_unfamiliar

pytestmark = pytest.mark.llm
GEN = Path(__file__).resolve().parents[1] / "data/generated"


@pytest.mark.skipif(not (os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_AUTH_TOKEN")),
                    reason="no Anthropic credentials")
def test_claude_maps_unfamiliar_headers_without_values(tmp_path):
    write_unfamiliar(GEN / "sample.xlsx", tmp_path)
    sheet = read_sheet(tmp_path / "sample.xlsx")
    proposal = ClaudeProposer(send_values=False).propose(sheet, INVOICE_VOUCHING)
    assert proposal is not None and proposal.sent_values is False
    required = {f.name for f in INVOICE_VOUCHING.required}
    assert {f: c for f, c in proposal.columns.items() if f in required} == {f: EXPECTED[f] for f in required}
    assert validate(sheet, INVOICE_VOUCHING, to_mapping(sheet, INVOICE_VOUCHING, proposal)) == []
