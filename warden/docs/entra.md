# Signing in with real Microsoft Entra ID (step 14)

Warden accepts Entra ID tokens as soon as `Entra:TenantId` and `Entra:ClientId` are set
(`src/Warden.Api/Auth/EntraSignIn.cs`). The tests prove the logic against a fake Entra; this page
is the one-time setup to prove it against the real one. About 10 minutes in the Azure portal.

## 1. Register Warden as an app

Azure portal → **Microsoft Entra ID** → **App registrations** → **New registration**
- Name: `Warden`
- Supported account types: **Accounts in this organizational directory only** (single tenant)
- Redirect URI: leave empty (Warden is an API; nobody signs in *on* it)

From the app's **Overview**, copy:
- **Application (client) ID** → `Entra:ClientId`
- **Directory (tenant) ID** → `Entra:TenantId`

## 2. Expose an API

App → **Expose an API**
1. **Application ID URI** → Add → accept `api://<client id>` → Save
2. **Add a scope**: name `access_as_user`, who can consent: Admins and users, fill in the
   display names → Add scope
3. **Add a client application**: `04b07795-8ddb-461a-bbee-02f9e1bf7b46` (the Azure CLI), tick the
   `access_as_user` scope. This lets you get a token with `az` below without writing a client.

Optional: App → **Manifest** → `"requestedAccessTokenVersion": 2`. Warden accepts v1 and v2
tokens from your tenant either way.

## 3. Tell Warden

Keep the ids out of git: `appsettings.Development.json` is gitignored.

```json
{ "Entra": { "TenantId": "<tenant id>", "ClientId": "<client id>" } }
```

## 4. Link your account to a demo user

Warden's users have their own ids; an Entra account is linked to one of them.

```bash
az login --tenant <tenant id>
OID=$(az ad signed-in-user show --query id -o tsv)
dotnet run --project src/Warden.Api -- link-entra sara <tenant id> $OID
```

## 5. Try it

```bash
dotnet run --project src/Warden.Api --urls http://localhost:5000      # terminal 1
TOKEN=$(az account get-access-token --scope api://<client id>/access_as_user --query accessToken -o tsv)
curl -s localhost:5000/me -H "Authorization: Bearer $TOKEN"
# -> {"userId":"00000000-...-000000000001","tenantId":"a0000000-...-00000000000a","name":"Sara"}
cd ../tickmark && make agent TOKEN=$TOKEN                               # Tickmark (on :8000) vouches Coke as you
```

`/me` shows **Warden's** ids for Sara, not your Entra ids: from here on, everything (decision,
passes, audit log) is the same code as with dev tokens.

## What can go wrong

| Symptom | Cause |
|---|---|
| 401, log says `IDX10214` (audience) | token not for Warden: use `--scope api://<client id>/access_as_user` |
| 401, log says `IDX10205` (issuer) | token from another tenant: `az login --tenant <tenant id>` |
| 401, log says `not linked to a Warden user` | step 4 not done, or done for another account |
| 401 with no detail, first request is slow | Warden can't reach `login.microsoftonline.com` to fetch Entra's keys |
| `AADSTS65001` from `az` | step 2.3 (Azure CLI as a client application) missing |
