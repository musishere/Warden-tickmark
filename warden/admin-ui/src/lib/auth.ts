/*
 * auth.ts: Microsoft sign-in for the admin page. OAuth 2.0 authorization code + PKCE, straight with
 * Microsoft Entra (what MSAL does, by hand):
 *   1. GET /admin/sign-in -> Warden's client id, tenant, scope
 *   2. redirect to Microsoft's sign-in page; it comes back to /admin/?code=...
 *   3. POST the code to Microsoft's token endpoint (allowed from a browser because Warden's app
 *      registration lists this page as a "Single-page application" redirect URI)
 * The token is issued to Warden's own app (azp = Warden), which /admin requires; an agent's token never
 * is. It lives in sessionStorage (gone when the tab closes); no refresh token: after an hour Warden
 * says 401 and the page signs in again.
 */
const TOKEN = "warden.token";
const PKCE = "warden.pkce";

type SignInSettings = { tenantId: string; clientId: string; scope: string };
type Pending = SignInSettings & { verifier: string; state: string };

const b64url = (bytes: ArrayBuffer | Uint8Array) =>
  btoa(String.fromCharCode(...new Uint8Array(bytes))).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
const random = () => b64url(crypto.getRandomValues(new Uint8Array(32)));
const redirectUri = () => `${location.origin}/admin/`;

export const getToken = () => sessionStorage.getItem(TOKEN);

export async function signIn() {
  const res = await fetch("/admin/sign-in");
  if (!res.ok) throw new Error("Microsoft sign-in isn't set up on this Warden (Entra:TenantId / Entra:ClientId).");
  const s: SignInSettings = await res.json();
  const pending: Pending = { ...s, verifier: random(), state: random() };
  sessionStorage.setItem(PKCE, JSON.stringify(pending));
  const challenge = b64url(await crypto.subtle.digest("SHA-256", new TextEncoder().encode(pending.verifier)));
  location.assign(`https://login.microsoftonline.com/${s.tenantId}/oauth2/v2.0/authorize?` + new URLSearchParams({
    client_id: s.clientId, response_type: "code", redirect_uri: redirectUri(), response_mode: "query",
    scope: `${s.scope} openid`, state: pending.state, code_challenge: challenge, code_challenge_method: "S256",
    prompt: "select_account",
  }));
}

// Called on load: finishes a sign-in Microsoft just sent us back from, if any. Throws Microsoft's error.
export async function finishSignIn() {
  const params = new URLSearchParams(location.search);
  if (params.get("error")) {
    history.replaceState(null, "", "/admin/");
    throw new Error(params.get("error_description") || params.get("error")!);
  }
  if (!params.get("code")) return;
  const saved: Pending | null = JSON.parse(sessionStorage.getItem(PKCE) || "null");
  sessionStorage.removeItem(PKCE);
  history.replaceState(null, "", "/admin/");
  if (!saved || params.get("state") !== saved.state) throw new Error("That sign-in didn't start in this tab. Please sign in again.");
  const res = await fetch(`https://login.microsoftonline.com/${saved.tenantId}/oauth2/v2.0/token`, {
    method: "POST",
    body: new URLSearchParams({
      client_id: saved.clientId, grant_type: "authorization_code", code: params.get("code")!,
      redirect_uri: redirectUri(), code_verifier: saved.verifier, scope: `${saved.scope} openid`,
    }),
  });
  const body = await res.json();
  if (!res.ok) throw new Error(body.error_description || body.error);
  sessionStorage.setItem(TOKEN, body.access_token);
}

export function signOut() {
  sessionStorage.clear();
}
