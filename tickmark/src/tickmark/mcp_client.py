"""A plain MCP client: talk to ONE MCP server (Streamable HTTP, JSON-RPC 2.0) with a bearer token.

Nothing in here knows about Warden. Tickmark's agent points it at Warden's address for an
engagement (/mcp/engagements/{id}) with the user's sign-in token; Warden makes the pass, checks
every call and logs it. Pointed at any other MCP server, it works the same.

    with McpClient(url, token) as mcp:
        mcp.call("list_documents", {})   -> the tool's answer (its JSON text, parsed)

Fail closed: anything but a clean result (401/403, an isError result, a JSON-RPC error, the server
unreachable) raises Refused with the reason, and the agent stops.
ponytail: plain-JSON answers only (no text/event-stream), no paging of tools/list; Warden answers JSON.
"""
import json
from typing import Self

import httpx

VERSION = "2025-06-18"


class Refused(Exception):
    pass


class McpClient:
    def __init__(self, url: str, token: str, http: httpx.Client | None = None):
        self._url = url
        self._http = http or httpx.Client(timeout=300)  # run_vouch reads every PDF
        self._headers = {"Authorization": f"Bearer {token}", "Accept": "application/json, text/event-stream"}
        self._id = 0

    def __enter__(self) -> Self:
        self._rpc("initialize", {"protocolVersion": VERSION, "capabilities": {},
                                 "clientInfo": {"name": "tickmark", "version": "0.1"}})
        self._send({"jsonrpc": "2.0", "method": "notifications/initialized"})
        return self

    def __exit__(self, *exc) -> None:
        self._http.close()

    def tools(self) -> list[str]:
        return [t["name"] for t in self._rpc("tools/list", {})["tools"]]

    def call(self, tool: str, arguments: dict):
        """The tool's answer: its structuredContent if it sent one, else its text (parsed if JSON)."""
        result = self._rpc("tools/call", {"name": tool, "arguments": arguments})
        text = "".join(c.get("text", "") for c in result.get("content", []))
        if result.get("isError"):
            raise Refused(f"{tool}: {text}")
        if "structuredContent" in result:
            return result["structuredContent"]
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            return text

    def _rpc(self, method: str, params: dict) -> dict:
        self._id += 1
        body = self._send({"jsonrpc": "2.0", "id": self._id, "method": method, "params": params}).json()
        if "error" in body:
            raise Refused(f"{method}: {body['error'].get('message')}")
        return body["result"]

    def _send(self, msg: dict) -> httpx.Response:
        try:
            r = self._http.post(self._url, json=msg, headers=self._headers)
        except httpx.HTTPError as e:  # unreachable -> deny
            raise Refused(f"server unavailable: {e}") from e
        if r.status_code >= 300:
            raise Refused(f"{r.status_code} {r.text}")
        return r
