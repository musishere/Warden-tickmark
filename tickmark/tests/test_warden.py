"""Tickmark behind Warden, without a running Warden.

Tools (api/tools.py): only a Warden-forwarded token for that tool gets in. A key pair made here
stands in for Warden's (signing_key is swapped for its public half).
Client (warden_client.py): anything but a 200 from Warden, or no Warden at all, is WardenDenied.
"""
import time
import uuid

import httpx
import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi.testclient import TestClient

from tickmark.api import tools
from tickmark.api.main import app
from tickmark.config import settings
from tickmark.warden_client import WardenClient, WardenDenied
from tools.datagen.demo import easy

WARDEN_KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)
OTHER_KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)
COKE = "e0000000-0000-0000-0000-000000000001"
client = TestClient(app)


@pytest.fixture(autouse=True)
def warden_key_and_files(tmp_path, monkeypatch):
    monkeypatch.setattr(tools, "signing_key", lambda token: WARDEN_KEY.public_key())
    monkeypatch.setattr(settings, "engagements_dir", str(tmp_path))
    easy(tmp_path / COKE)  # sample.xlsx + invoices/*.pdf for Coke


def forwarded(tool: str, key=WARDEN_KEY, **override) -> dict:
    claims = {"iss": "warden", "aud": "tickmark-tools", "exp": int(time.time()) + 60, "sub": "sara",
              "act": {"sub": "tickmark"}, "tid": str(uuid.uuid4()), "eng": COKE, "tool": tool} | override
    return {"Authorization": f"Bearer {jwt.encode(claims, key, algorithm='RS256')}"}


def test_warden_forwarded_call_gets_the_engagements_documents():
    r = client.post("/tools/list_documents", json={}, headers=forwarded("list_documents"))
    assert r.status_code == 200
    assert "inv_101.pdf" in [d["name"] for d in r.json()["documents"]]


@pytest.mark.parametrize("headers", [
    {},                                                      # no token: an agent skipping Warden
    forwarded("list_documents", key=OTHER_KEY),              # not Warden's signature
    forwarded("list_documents", aud="warden-tools"),         # the agent's own pass, sent straight here
    forwarded("list_documents", exp=int(time.time()) - 1),   # expired
])
def test_anything_but_a_warden_forwarded_token_is_401(headers):
    assert client.post("/tools/list_documents", json={}, headers=headers).status_code == 401


def test_token_for_one_tool_does_not_open_another():
    r = client.post("/tools/run_vouch", json={}, headers=forwarded("list_documents"))
    assert r.status_code == 403


def test_engagement_comes_from_the_token_and_names_cannot_escape_its_folder():
    other = forwarded("list_documents", eng=str(uuid.uuid4()))  # an engagement with no files here
    assert client.post("/tools/list_documents", json={}, headers=other).json() == {"documents": []}
    r = client.post("/tools/read_document", json={"name": "../sample.xlsx"}, headers=forwarded("read_document"))
    assert r.status_code == 404


def test_run_vouch_through_the_tool():
    r = client.post("/tools/run_vouch", json={"period": "2025-01-01:2025-12-31", "currency": "EUR"},
                    headers=forwarded("run_vouch"))
    assert r.status_code == 200
    assert " ".join(v["row_id"] + v["mark"] for v in r.json()["verdicts"]) == "E-1✅ E-2✅ E-3✅ E-4✅ E-5⚠️ E-6❓"


def fake_warden(status: int, body: dict) -> WardenClient:
    http = httpx.Client(base_url="http://warden", transport=httpx.MockTransport(lambda req: httpx.Response(status, json=body)))
    return WardenClient("http://warden", "sign-in", http=http)


def test_client_stops_on_any_warden_no():
    with pytest.raises(WardenDenied, match="ethical wall"):
        fake_warden(403, {"reason": "ethical wall: Works on Coke 2026"}).exchange_token("documents.read", COKE)
    with pytest.raises(WardenDenied, match="no delegated token"):
        fake_warden(200, {}).invoke("list_documents", {})


def test_client_stops_when_warden_is_down():
    def down(req):
        raise httpx.ConnectError("connection refused")

    http = httpx.Client(base_url="http://warden", transport=httpx.MockTransport(down))
    with pytest.raises(WardenDenied, match="warden unavailable"):
        WardenClient("http://warden", "sign-in", http=http).exchange_token("documents.read", COKE)
