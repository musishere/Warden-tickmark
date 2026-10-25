"""Tickmark behind Warden, without a running Warden.

Tools (api/tools.py): only a Warden-forwarded token for that tool gets in. A key pair made here
stands in for Warden's (signing_key is swapped for its public half).
Client (mcp_client.py): anything but a clean MCP result from Warden, or no Warden at all, is Refused.
"""
import json
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
from tickmark.mcp_client import McpClient, Refused
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


def fake_server(answer) -> McpClient:
    """An MCP server that answers initialize + the notification, and `answer(msg)` for the rest."""
    def handle(req: httpx.Request) -> httpx.Response:
        msg = json.loads(req.content)
        if msg["method"] == "initialize":
            return httpx.Response(200, json={"jsonrpc": "2.0", "id": msg["id"], "result": {"protocolVersion": "2025-06-18"}})
        if "id" not in msg:
            return httpx.Response(202)
        return answer(msg)
    return McpClient("http://warden/mcp/engagements/coke", "sign-in", http=httpx.Client(transport=httpx.MockTransport(handle)))


def result(msg: dict, text: str, error: bool = False) -> httpx.Response:
    return httpx.Response(200, json={"jsonrpc": "2.0", "id": msg["id"],
                                     "result": {"content": [{"type": "text", "text": text}], "isError": error}})


def test_client_sends_the_sign_in_token_and_returns_the_tools_answer():
    seen = []

    def answer(msg):
        seen.append(msg)
        return result(msg, '{"documents": []}')

    with fake_server(answer) as mcp:
        assert mcp.call("list_documents", {}) == {"documents": []}
    assert seen[0]["params"] == {"name": "list_documents", "arguments": {}}


def test_client_takes_any_servers_answer_not_only_json_text():
    """Other MCP servers (step 19-3) answer with structuredContent or plain text."""
    def answer(msg):
        if msg["params"]["name"] == "read_note":
            return result(msg, "Two new suppliers this year")
        return httpx.Response(200, json={"jsonrpc": "2.0", "id": msg["id"], "result": {
            "content": [{"type": "text", "text": "kickoff"}], "structuredContent": {"result": ["kickoff"]}}})

    with fake_server(answer) as mcp:
        assert mcp.call("list_notes", {}) == {"result": ["kickoff"]}
        assert mcp.call("read_note", {"title": "risks"}) == "Two new suppliers this year"


def test_client_stops_on_any_warden_no():
    wall = fake_server(lambda m: httpx.Response(403, json={"reason": "ethical wall: Works on Coke 2026"}))
    with pytest.raises(Refused, match="ethical wall"), wall as mcp:  # refused before any tool: 403
        mcp.call("list_documents", {})
    no_scope = fake_server(lambda m: result(m, "pass does not include vouch.run", error=True))
    with pytest.raises(Refused, match="pass does not include"), no_scope as mcp:  # a refused call: isError
        mcp.call("run_vouch", {})


def test_client_stops_when_warden_is_down():
    def down(req):
        raise httpx.ConnectError("connection refused")

    with pytest.raises(Refused, match="server unavailable"):
        McpClient("http://warden/mcp", "sign-in", http=httpx.Client(transport=httpx.MockTransport(down))).__enter__()
