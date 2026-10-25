"""Tickmark's tools as an MCP server: POST /mcp (JSON-RPC 2.0, plain JSON answers, no sessions).

This is how Warden reaches Tickmark from step 18b on, the same way it reaches any firm's MCP
server (warden/src/Warden.Api/Auth/UpstreamMcp.cs). Tickmark is just one server behind Warden.

    initialize                 -> protocol version + "I offer tools"
    notifications/initialized  -> 202, no body
    tools/list                 -> the three tools, with descriptions + input schemas (Warden passes
                                  these on to agents; which ones an agent sees is Warden's call)
    tools/call                 -> runs the tool; its answer as JSON text. A tool error (no such
                                  document, bad period...) -> isError: true + the message

Only Warden may call this: every message needs a Warden-forwarded token (tools.warden_claims), and
tools/call only runs the tool that token was made for (tools.warden_call). Which engagement:
ALWAYS the token's, never the arguments.
"""
import json

from fastapi import APIRouter, Header, HTTPException
from fastapi.responses import JSONResponse, Response
from pydantic import ValidationError

from tickmark.api import tools

router = APIRouter()
VERSIONS = ["2025-11-25", "2025-06-18", "2025-03-26"]
NO_ARGS = {"type": "object", "properties": {}}

# name -> (description, input schema, run(arguments, call) -> dict)
TOOLS = {
    "list_documents": ("List the documents (invoices) in this engagement's folder.", NO_ARGS,
                       lambda args, call: tools.list_documents(call)),
    "read_document": ("Read one document from this engagement's folder: its text, page by page.",
                      tools.ReadIn.model_json_schema(),
                      lambda args, call: tools.read_document(tools.ReadIn(**args), call)),
    "run_vouch": ("Vouch this engagement's ledger sample against its invoices and return a verdict per row.",
                  tools.VouchIn.model_json_schema(),
                  lambda args, call: tools.vouch(tools.VouchIn(**args), call)),
}


@router.post("/mcp")
def mcp(msg: dict, authorization: str = Header("")) -> Response:
    method, id, params = msg.get("method"), msg.get("id"), msg.get("params") or {}
    tools.warden_claims(authorization, f"mcp {method}")  # 401 unless Warden sent it

    if id is None:  # a notification: nothing to answer
        return Response(status_code=202)
    if method == "initialize":
        asked = params.get("protocolVersion")
        return reply(id, {"protocolVersion": asked if asked in VERSIONS else VERSIONS[0],
                          "capabilities": {"tools": {}}, "serverInfo": {"name": "tickmark", "version": "0.1"}})
    if method == "tools/list":
        return reply(id, {"tools": [{"name": n, "description": d, "inputSchema": s} for n, (d, s, _) in TOOLS.items()]})
    if method == "tools/call" and params.get("name") in TOOLS:
        name = params["name"]
        call = tools.warden_call(authorization, name)  # 403 if the token is for another tool
        try:
            out, error = TOOLS[name][2](params.get("arguments") or {}, call), False
        except HTTPException as e:  # the tool said no: missing document, bad period...
            out, error = {"error": e.detail}, True
        except ValidationError as e:  # arguments of the wrong shape
            out, error = {"error": str(e)}, True
        return reply(id, {"content": [{"type": "text", "text": json.dumps(out)}], "isError": error})
    if method == "tools/call":
        return reply(id, {"content": [{"type": "text", "text": f"unknown tool: {params.get('name')}"}], "isError": True})
    return JSONResponse({"jsonrpc": "2.0", "id": id, "error": {"code": -32601, "message": f"method not found: {method}"}})


def reply(id, result: dict) -> JSONResponse:
    return JSONResponse({"jsonrpc": "2.0", "id": id, "result": result})
