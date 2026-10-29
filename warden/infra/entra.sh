#!/usr/bin/env bash
# entra.sh — register Warden in Entra (how-it-works §22 items 1, 2, 7, 8). Safe to re-run: reuses the app if it exists.
set -euo pipefail

APP_ID=$(az ad app list --display-name Warden --query "[0].appId" -o tsv)
[ -n "$APP_ID" ] || APP_ID=$(az ad app create --display-name Warden --sign-in-audience AzureADMyOrg --query appId -o tsv)
OBJ_ID=$(az ad app show --id "$APP_ID" --query id -o tsv)

# Keep ids stable across re-runs.
SCOPE_ID=$(az ad app show --id "$APP_ID" --query "api.oauth2PermissionScopes[?value=='access_as_user'].id | [0]" -o tsv)
[ -n "$SCOPE_ID" ] || SCOPE_ID=$(cat /proc/sys/kernel/random/uuid)
ROLE_ID=$(az ad app show --id "$APP_ID" --query "appRoles[?value=='Warden.Admin'].id | [0]" -o tsv)
[ -n "$ROLE_ID" ] || ROLE_ID=$(cat /proc/sys/kernel/random/uuid)

# Item 2 first: scope must exist before it can be pre-authorized.
az rest --method PATCH --uri "https://graph.microsoft.com/v1.0/applications/$OBJ_ID" --headers Content-Type=application/json --body "{
  \"identifierUris\": [\"api://$APP_ID\"],
  \"api\": {
    \"requestedAccessTokenVersion\": 2,
    \"oauth2PermissionScopes\": [{ \"id\": \"$SCOPE_ID\", \"value\": \"access_as_user\", \"type\": \"User\", \"isEnabled\": true,
      \"adminConsentDisplayName\": \"Use Warden as you\", \"adminConsentDescription\": \"Lets the app call Warden as the signed-in user.\",
      \"userConsentDisplayName\": \"Use Warden as you\", \"userConsentDescription\": \"Lets the app call Warden as you.\" }]
  },
  \"appRoles\": [{ \"id\": \"$ROLE_ID\", \"value\": \"Warden.Admin\", \"displayName\": \"Warden Admin\", \"description\": \"Manages a firm in Warden\",
    \"allowedMemberTypes\": [\"User\"], \"isEnabled\": true }],
  \"spa\": { \"redirectUris\": [\"http://localhost:5000/admin/\"] }
}"
# Azure CLI (04b07795-…) may get tokens for access_as_user without a consent prompt.
az rest --method PATCH --uri "https://graph.microsoft.com/v1.0/applications/$OBJ_ID" --headers Content-Type=application/json --body "{
  \"api\": { \"preAuthorizedApplications\": [{ \"appId\": \"04b07795-8ddb-461a-bbee-02f9e1bf7b46\", \"delegatedPermissionIds\": [\"$SCOPE_ID\"] }] }
}"

# Item 7: the enterprise app + Warden Admin role for you.
SP_ID=$(az ad sp show --id "$APP_ID" --query id -o tsv 2>/dev/null || az ad sp create --id "$APP_ID" --query id -o tsv)
ME=$(az ad signed-in-user show --query id -o tsv)
HAS=$(az rest --method GET --uri "https://graph.microsoft.com/v1.0/servicePrincipals/$SP_ID/appRoleAssignedTo" --query "value[?principalId=='$ME' && appRoleId=='$ROLE_ID'] | length(@)" -o tsv)
[ "$HAS" != "0" ] || az rest --method POST --uri "https://graph.microsoft.com/v1.0/servicePrincipals/$SP_ID/appRoleAssignedTo" --headers Content-Type=application/json \
  --body "{ \"principalId\": \"$ME\", \"resourceId\": \"$SP_ID\", \"appRoleId\": \"$ROLE_ID\" }" -o none

echo "ENTRA_TENANT_ID=$(az account show --query tenantId -o tsv)"
echo "ENTRA_CLIENT_ID=$APP_ID"
echo "MY_OBJECT_ID=$ME"
