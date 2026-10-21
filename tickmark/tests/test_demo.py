"""The two demo cases (tools.datagen.demo) give what their expected.json says, with Azure off and
with Azure in auto mode (faked from the case's labels: what Azure would read)."""
from datetime import date

from tickmark.definitions import INVOICE_VOUCHING as D
from tickmark.extract import LabelExtractor
from tickmark.extract.router import Router
from tickmark.fields.mapping import ColumnMapping, Meaning, headers_sha256
from tickmark.fields.sheet import read_sheet
from tickmark.pipeline import run_vouch
from tickmark.vouch import MARK, VouchPolicy
from tools.datagen.demo import easy, tough

POLICY = VouchPolicy(period_start=date(2025, 1, 1), period_end=date(2025, 12, 31), currency="EUR")


class FakeAzure:
    def __init__(self, labels):
        self.labels = LabelExtractor(labels)

    def extract(self, p):
        return self.labels.extract(p)


def marks(run) -> str:
    return " ".join(f"{v.row_id}{MARK[v.verdict]}" for v in run.verdicts)


def test_easy_case(tmp_path):
    easy(tmp_path)
    run = run_vouch(tmp_path / "sample.xlsx", tmp_path / "invoices", Router("off"), POLICY)
    assert marks(run) == "E-1✅ E-2✅ E-3✅ E-4✅ E-5⚠️ E-6❓"


def test_tough_case_with_azure_off_and_auto(tmp_path):
    tough(tmp_path)
    sheet = read_sheet(tmp_path / "sample.xlsx")
    m = ColumnMapping(definition=D.name, version=D.version, source="person", confirmed_by="t",
                      headers_sha256=headers_sha256(sheet),
                      columns={"row_id": "A", "vendor": "B", "reference": "C", "invoice_date": "D", "net_amount": "E",
                               "amount": "F", "currency": "G", "contract_no": "H"},
                      meanings={"contract_no": Meaning(type="identifier", labels=["Contract"])})
    off = run_vouch(tmp_path / "sample.xlsx", tmp_path / "invoices", Router("off"), POLICY, m)
    assert sheet.header_row == 4
    assert [s.row_number for s in off.loaded.skipped] == [1, 2, 18] and [e.row_number for e in off.loaded.errors] == [16]
    assert marks(off) == "T-01✅ T-02✅ T-03⚠️ T-04❓ T-05👀 T-06👀 T-07⚠️ T-08⚠️ T-09👀 T-10⚠️ T-11✅ T-13❓"
    auto = run_vouch(tmp_path / "sample.xlsx", tmp_path / "invoices", Router("auto", FakeAzure(tmp_path / "labels")),
                     POLICY, m)
    assert marks(auto) == "T-01✅ T-02✅ T-03⚠️ T-04✅ T-05✅ T-06👀 T-07⚠️ T-08⚠️ T-09⚠️ T-10⚠️ T-11✅ T-13❓"
    assert sorted(f for f, (by, _) in auto.routes.items() if by == "azure") == ["t04.pdf", "t05.pdf", "t06.pdf", "t09.pdf",
                                                                                    "t10.pdf"]
    contract = next(a for a in auto.verdicts[0].agreements if a.field == "contract no")
    assert contract.result == "agrees" and contract.pdf_value == "CT-7781"
