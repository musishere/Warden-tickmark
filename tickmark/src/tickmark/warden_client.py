"""Every tool call goes through Warden: authorize, log, forward. Fail closed.

exchange_token: trade the user's sign-in token for a narrow, 15-minute pass for one engagement.
invoke: call a tool through Warden's gate with that pass.
Anything but a 200 (denied, revoked, Warden down, tool down) raises WardenDenied: the agent stops.
"""
import httpx

from tickmark.config import settings


class WardenDenied(Exception):
    pass


class WardenClient:
    def __init__(self, base_url: str, user_token: str, agent_id: str = settings.agent_id,
                 http: httpx.Client | None = None):
        self._http = http or httpx.Client(base_url=base_url, timeout=300)  # run_vouch reads every PDF
        self._user_token = user_token
        self._agent_id = agent_id
        self._agent_token: str | None = None

    def exchange_token(self, scope: str, resource: str) -> None:
        r = self._post("/token/exchange", {"agent_id": self._agent_id, "scope": scope, "resource": resource},
                       self._user_token)
        self._agent_token = r.json()["access_token"]

    def invoke(self, tool: str, payload: dict) -> dict:
        if not self._agent_token:
            raise WardenDenied("no delegated token")
        return self._post(f"/tools/{tool}/invoke", payload, self._agent_token).json()

    def _post(self, path: str, body: dict, token: str) -> httpx.Response:
        try:
            r = self._http.post(path, json=body, headers={"Authorization": f"Bearer {token}"})
        except httpx.HTTPError as e:  # Warden unreachable -> deny
            raise WardenDenied(f"warden unavailable: {e}") from e
        if r.status_code != 200:
            raise WardenDenied(f"{r.status_code} {r.text}")
        return r
