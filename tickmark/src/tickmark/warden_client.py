"""Every tool call goes through Warden: authorize, meter, log. Fail closed."""
import httpx


class WardenDenied(Exception):
    pass


class WardenClient:
    def __init__(self, base_url: str, user_token: str):
        self._http = httpx.Client(base_url=base_url, timeout=10)
        self._user_token = user_token
        self._agent_token: str | None = None

    def exchange_token(self, scope: str, resource: str) -> None:
        r = self._http.post(
            "/token/exchange",
            json={"scope": scope, "resource": resource},
            headers={"Authorization": f"Bearer {self._user_token}"},
        )
        r.raise_for_status()
        self._agent_token = r.json()["access_token"]

    def invoke(self, tool: str, payload: dict) -> dict:
        if not self._agent_token:
            raise WardenDenied("no delegated token")
        try:
            r = self._http.post(
                f"/tools/{tool}/invoke",
                json=payload,
                headers={"Authorization": f"Bearer {self._agent_token}"},
            )
        except httpx.HTTPError as e:  # Warden unreachable -> deny
            raise WardenDenied(f"warden unavailable: {e}") from e
        if r.status_code != 200:
            raise WardenDenied(r.text)
        return r.json()
