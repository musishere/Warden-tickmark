# /// script
# requires-python = ">=3.12"
# dependencies = ["mcp==2.3.0", "pyjwt[crypto]"]
# ///
"""A firm's notes, as an MCP server behind Warden (step 19-3). Nothing to do with Tickmark.

Proof that Warden fronts ANY MCP server: this one is written with the official MCP Python SDK,
default settings (streamable HTTP, sessions, event-stream answers). The only Warden-aware part is
the usual one for any OAuth-protected MCP server: whose tokens to trust (WardenTokens below).

    uv run examples/notes-server/server.py          # http://localhost:8100/mcp

Then connect it behind Warden (from src/Warden.Api):
    dotnet run -- add-upstream "Firm A" notes http://localhost:8100/mcp firm-a-notes
    dotnet run -- assign-tool  "Firm A" notes list_notes documents.read
    dotnet run -- assign-tool  "Firm A" notes read_note  documents.read
delete_all_notes is never assigned, so no agent ever sees it.

In Azure (step 25) it runs as a container with INTERNAL ingress only: no public address, so only
Warden can reach it (step 20). Settings come from the environment there:
    WARDEN_URL   Warden's address, for its JWKS        (default http://localhost:5000)
    AUDIENCE     the aud it accepts                    (default firm-a-notes)
    PUBLIC_URL   its own MCP address                   (default http://localhost:8100/mcp)
    HOST, PORT   where to listen                       (default 127.0.0.1, 8100)

Which engagement: ALWAYS the token's `eng` claim, never the arguments.
ponytail: notes are a hard-coded dict; a real server would keep them in a database.
"""
import os

import jwt
from mcp.server.auth.middleware.auth_context import get_access_token
from mcp.server.auth.provider import AccessToken
from mcp.server.auth.settings import AuthSettings
from mcp.server.mcpserver import MCPServer

WARDEN = os.environ.get("WARDEN_URL", "http://localhost:5000")
AUDIENCE = os.environ.get("AUDIENCE", "firm-a-notes")  # must match add-upstream's last argument
PUBLIC_URL = os.environ.get("PUBLIC_URL", "http://localhost:8100/mcp")
COKE, PEPSI = "e0000000-0000-0000-0000-000000000001", "e0000000-0000-0000-0000-000000000002"
NOTES = {
    COKE: {"kickoff": "Client wants the vouch done by 15 Nov. Contact: J. Smith (AP).",
           "risks": "Two new suppliers this year; check E-5 closely."},
    PEPSI: {"kickoff": "Pepsi team: separate staff only (ethical wall with Coke)."},
}


class WardenTokens:
    """Accept only Warden's one-minute forward tokens made for this server (aud = AUDIENCE)."""

    jwks = jwt.PyJWKClient(f"{WARDEN}/.well-known/jwks.json")

    async def verify_token(self, token: str) -> AccessToken | None:
        try:
            key = self.jwks.get_signing_key_from_jwt(token).key
            c = jwt.decode(token, key, algorithms=["RS256"], issuer="warden", audience=AUDIENCE,
                           options={"require": ["exp", "sub", "eng"]})
        except jwt.PyJWTError:  # bad signature, expired, someone else's token, Warden unreachable
            return None  # -> 401
        return AccessToken(token=token, client_id=c["act"]["sub"], scopes=c.get("scope", "").split(),
                           expires_at=c["exp"], subject=c["sub"], claims=c)


mcp = MCPServer("firm-notes", token_verifier=WardenTokens(),
                auth=AuthSettings(issuer_url=WARDEN, resource_server_url=PUBLIC_URL,
                                  validate_token_resource=False))  # False: WardenTokens checks aud itself


def engagement_notes() -> dict[str, str]:
    return NOTES.get(get_access_token().claims["eng"], {})


@mcp.tool()
def list_notes() -> list[str]:
    """The titles of this engagement's notes."""
    return list(engagement_notes())


@mcp.tool()
def read_note(title: str) -> str:
    """One note of this engagement, by title."""
    notes = engagement_notes()
    if title not in notes:
        raise ValueError(f"no note called {title!r}")
    return notes[title]


@mcp.tool()
def delete_all_notes() -> str:
    """Delete every note. Never assigned in Warden, so agents can't see or call it."""
    return "would have deleted everything"


if __name__ == "__main__":
    mcp.run("streamable-http", host=os.environ.get("HOST", "127.0.0.1"), port=int(os.environ.get("PORT", "8100")))
