"""Tickmark's tools as an MCP server (api/mcp.py), as Warden reaches them: POST /mcp, JSON-RPC.

Same rule as /tools/*: only a Warden-forwarded token gets in, and tools/call only for the tool the
token names. Uses test_warden's stand-in Warden key and Coke's demo files.
"""
import pytest

from tests.test_warden import client, forwarded, warden_key_and_files  # noqa: F401  (the fixture)


def rpc(method: str, headers: dict, params: dict | None = None, id: int | None = 1):
    msg = {"jsonrpc": "2.0", "method": method, "params": params or {}}
    if id is not None:
        msg["id"] = id
    return client.post("/mcp", json=msg, headers=headers)


def test_initialize_and_list_describe_the_three_tools():
    h = forwarded("tools/list")
    init = rpc("initialize", h, {"protocolVersion": "2025-06-18"}).json()["result"]
    assert init["protocolVersion"] == "2025-06-18"
    assert "tools" in init["capabilities"]
    assert rpc("notifications/initialized", h, id=None).status_code == 202

    tools = rpc("tools/list", h).json()["result"]["tools"]
    assert [t["name"] for t in tools] == ["list_documents", "read_document", "run_vouch"]
    read = next(t for t in tools if t["name"] == "read_document")
    assert read["description"] and read["inputSchema"]["required"] == ["name"]


def test_tools_call_runs_the_tool_for_the_tokens_engagement():
    r = rpc("tools/call", forwarded("list_documents"), {"name": "list_documents", "arguments": {}}).json()["result"]
    assert r["isError"] is False
    assert "inv_101.pdf" in r["content"][0]["text"]

    r = rpc("tools/call", forwarded("run_vouch"),
            {"name": "run_vouch", "arguments": {"period": "2025-01-01:2025-12-31", "currency": "EUR"}}).json()["result"]
    assert "E-5" in r["content"][0]["text"]


def test_tool_errors_come_back_as_is_error():
    r = rpc("tools/call", forwarded("read_document"), {"name": "read_document", "arguments": {"name": "nope.pdf"}})
    assert r.json()["result"]["isError"] is True
    assert "no document nope.pdf" in r.json()["result"]["content"][0]["text"]
    r = rpc("tools/call", forwarded("read_document"), {"name": "read_document", "arguments": {}})  # name missing
    assert r.json()["result"]["isError"] is True


@pytest.mark.parametrize("headers", [{}, forwarded("tools/list", aud="warden-tools")])
def test_anything_but_a_warden_token_is_401(headers):
    assert rpc("tools/list", headers).status_code == 401


def test_token_for_one_tool_does_not_open_another():
    assert rpc("tools/call", forwarded("list_documents"), {"name": "run_vouch", "arguments": {}}).status_code == 403


def test_unknown_method_is_a_json_rpc_error():
    assert rpc("resources/list", forwarded("tools/list")).json()["error"]["code"] == -32601
