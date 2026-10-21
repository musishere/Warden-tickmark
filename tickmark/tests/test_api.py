"""The web app end to end: upload, column meanings, a run with its live log, saving to an engagement.
No network: Azure is off, the LLM is not called."""
import threading
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from tickmark import log
from tickmark.api.main import app
from tickmark.config import settings

ROOT = Path(__file__).resolve().parents[1]
GEN = ROOT / "data/generated"
client = TestClient(app)


@pytest.fixture(autouse=True)
def uploads(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "upload_dir", str(tmp_path / "uploads"))


def upload(sample: Path, pdfs: list[Path], **form) -> dict:
    files = [("sample", (sample.name, sample.read_bytes()))] + [("invoices", (p.name, p.read_bytes())) for p in pdfs]
    r = client.post("/api/workspaces", files=files, data=form)
    assert r.status_code == 200, r.text
    return r.json()


def wait(job: str) -> dict:
    for _ in range(300):
        j = client.get(f"/api/jobs/{job}").json()
        if j["status"] != "running":
            return j
        time.sleep(0.1)
    raise AssertionError("run did not finish")


def generated() -> tuple[Path, list[Path]]:
    if not (GEN / "sample.xlsx").exists():
        pytest.skip("generated data missing")
    return GEN / "sample.xlsx", sorted((GEN / "invoices").glob("*.pdf"))


def test_health_and_page():
    assert client.get("/health").json() == {"status": "ok"}
    assert "Tickmark" in client.get("/").text and client.get("/static/app.js").status_code == 200


def test_whole_flow_upload_confirm_run_with_its_log():
    sample, pdfs = generated()
    v = upload(sample, pdfs)
    assert v["header_row"] == 1 and len(v["pdfs"]) == 20 and v["problems"] == []
    assert v["mapping"]["columns"] == {"row_id": "A", "vendor": "B", "amount": "C", "invoice_date": "D"}
    c = client.put(f"/api/workspaces/{v['id']}/mapping", json={"columns": v["mapping"]["columns"],
                                                               "confirmed_by": "ali"}).json()
    assert c["confirmed"] and c["mapping"]["confirmed_by"] == "ali"

    r = client.post(f"/api/workspaces/{v['id']}/runs", json={"period": "2025-01-01:2025-12-31", "currency": "EUR",
                                                             "azure": "off"})
    j = wait(r.json()["job"])
    assert j["status"] == "done", j["error"]
    res = j["result"]
    assert res["counts"] == {"matched": 8, "difference": 4, "needs_review": 0, "no_evidence": 4}
    assert {x["read_by"] for x in res["routes"]} == {"local"} and len(res["routes"]) == 20
    steps = {line["step"] for line in j["logs"]}
    assert {"api", "sheet", "mapping", "read", "router", "identify", "vouch"} <= steps
    assert sum("✅ matched" in line["msg"] for line in j["logs"]) == 8
    assert client.get(f"/api/workspaces/{v['id']}/pdf/invoice_001.pdf").headers["content-type"] == "application/pdf"


def test_an_unknown_layout_says_what_is_missing_and_a_bad_mapping_is_not_confirmed(tmp_path):
    from tools.datagen.unfamiliar import write_unfamiliar

    sample, _ = generated()
    write_unfamiliar(sample, tmp_path)
    v = upload(tmp_path / "sample.xlsx", [])
    assert v["problems"] and v["problems"][0].startswith("cannot identify an invoice")
    bad = client.put(f"/api/workspaces/{v['id']}/mapping", json={
        "columns": {"vendor": "B", "amount": "A", "contract": "Z"}, "confirmed_by": "ali",
        "meanings": {"contract": {"type": "sql", "labels": []}}}).json()
    assert not bad["confirmed"] and bad["problems"]
    ok = client.put(f"/api/workspaces/{v['id']}/mapping", json={
        "columns": {"row_id": "A", "vendor": "B", "invoice_date": "C", "amount": "D"}, "confirmed_by": "ali"}).json()
    assert ok["confirmed"] and ok["origin"]["amount"] == "you"
    assert client.post(f"/api/workspaces/{v['id']}/suggest").status_code == 400  # LLM mapping is off


def test_runs_wait_for_settled_meanings_and_uploads_are_checked(tmp_path):
    from tools.datagen.unfamiliar import write_unfamiliar

    sample, _ = generated()
    write_unfamiliar(sample, tmp_path)
    v = upload(tmp_path / "sample.xlsx", [])
    assert client.post(f"/api/workspaces/{v['id']}/runs", json={"azure": "off"}).status_code == 400
    bad = client.post("/api/workspaces", files=[("sample", ("s.csv", b"a,b"))])
    assert bad.status_code == 400
    evil = client.post("/api/workspaces", files=[
        ("sample", ("s.xlsx", sample.read_bytes())), ("invoices", ("../../escape.pdf", b"%PDF-1.4"))]).json()
    assert evil["pdfs"] == ["escape.pdf"] and not (Path(settings.upload_dir).parent / "escape.pdf").exists()


def test_a_run_is_saved_to_an_engagement_and_read_back(tenancy):
    sample, pdfs = generated()
    app.state.db, app.state.admin_db = tenancy.app, tenancy.admin
    try:
        firm = client.post("/api/tenants", json={"name": "Firm C"}).json()["id"]
        eng = client.post(f"/api/tenants/{firm}/engagements", json={
            "client_name": "Initech", "period": "2025-01-01:2025-12-31", "currency": "EUR"}).json()["id"]
        assert any(e["engagement_id"] == eng for f in client.get("/api/tenants").json() for e in f["engagements"])
        v = upload(sample, pdfs[:6], tenant=firm, engagement=eng, user="ali")
        client.put(f"/api/workspaces/{v['id']}/mapping", json={"columns": v["mapping"]["columns"], "confirmed_by": "ali"})
        j = wait(client.post(f"/api/workspaces/{v['id']}/runs", json={"azure": "off", "save": True}).json()["job"])
        assert j["status"] == "done", j["error"]
        run_id = j["result"]["run_id"]
        assert j["result"]["policy"]["currency"] == "EUR"  # the engagement's settings were used
        listed = client.get(f"/api/engagements/{eng}/runs", params={"tenant": firm}).json()
        assert [r["id"] for r in listed] == [run_id]
        stored = client.get(f"/api/runs/{run_id}", params={"tenant": firm, "engagement": eng}).json()
        assert len(stored["verdicts"]) == len(j["result"]["verdicts"])
        assert {f["read_by"] for f in stored["files"]} == {"local"}
        row = stored["verdicts"][0]["row_id"]
        rv = client.post(f"/api/runs/{run_id}/reviews", json={"tenant": firm, "engagement": eng, "user": "sara",
                                                               "row_id": row, "decision": "accept"})
        assert rv.status_code == 200, rv.text
    finally:
        app.state.db = app.state.admin_db = None


def test_a_jobs_log_holds_only_its_own_lines():
    log.setup()
    a, b = log.capture("job-a"), log.capture("job-b")

    def work(job: str, n: int) -> None:
        log.job_id.set(job)
        for i in range(n):
            log.get("test").info("%s line %d", job, i)

    threads = [threading.Thread(target=work, args=("job-a", 3)), threading.Thread(target=work, args=("job-b", 2))]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert [x["msg"] for x in a] == ["job-a line 0", "job-a line 1", "job-a line 2"]
    assert [x["msg"] for x in b] == ["job-b line 0", "job-b line 1"] and b[0]["step"] == "test"


def test_an_upload_without_a_chosen_engagement_is_saved_under_the_local_default(tenancy, monkeypatch):
    sample, pdfs = generated()
    app.state.db, app.state.admin_db = tenancy.app, tenancy.admin
    monkeypatch.setattr(settings, "web_default_engagement", True)
    try:
        v = upload(sample, pdfs[:3], user="ali")
        assert v["engagement"] and v["tenant"]
        again = upload(sample, pdfs[:3], user="ali")
        assert (again["tenant"], again["engagement"]) == (v["tenant"], v["engagement"])  # created once, reused
        client.put(f"/api/workspaces/{v['id']}/mapping", json={"columns": v["mapping"]["columns"], "confirmed_by": "ali"})
        j = wait(client.post(f"/api/workspaces/{v['id']}/runs", json={"azure": "off", "save": True}).json()["job"])
        assert j["status"] == "done", j["error"] and j["result"]["run_id"]
        listed = client.get(f"/api/engagements/{v['engagement']}/runs", params={"tenant": v["tenant"]}).json()
        assert [r["id"] for r in listed] == [j["result"]["run_id"]]
    finally:
        app.state.db = app.state.admin_db = None
