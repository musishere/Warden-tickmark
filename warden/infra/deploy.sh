#!/usr/bin/env bash
# deploy.sh — put Warden in Azure, or update it (step 25). Safe to run again: same names, same secrets.
#
#   az login                       (once, as you; or GitHub Actions logs in for you)
#   ENTRA_TENANT_ID=... ENTRA_CLIENT_ID=... WARDEN_IMAGE=ghcr.io/<you>/warden:<tag> \
#   NOTES_IMAGE=ghcr.io/<you>/warden-notes:<tag> infra/deploy.sh
#
# Optional: RESOURCE_GROUP (default warden), LOCATION (default northeurope; pick another if the free
# trial says the region is restricted), DEPLOYER_OBJECT_ID + DEPLOYER_TYPE=ServicePrincipal (CI).
#
# Secrets are made ONCE and kept in Key Vault; later runs read them back, so the database password and
# Warden's signing key never change by accident (a new signing key would cancel every pass in flight).
set -euo pipefail
cd "$(dirname "$0")"

RG=${RESOURCE_GROUP:-warden}
LOCATION=${LOCATION:-northeurope}
: "${ENTRA_TENANT_ID:?set ENTRA_TENANT_ID}" "${ENTRA_CLIENT_ID:?set ENTRA_CLIENT_ID}"
: "${WARDEN_IMAGE:?set WARDEN_IMAGE}" "${NOTES_IMAGE:?set NOTES_IMAGE}"
DEPLOYER_OBJECT_ID=${DEPLOYER_OBJECT_ID:-$(az ad signed-in-user show --query id -o tsv)}
DEPLOYER_TYPE=${DEPLOYER_TYPE:-User}

az group create -n "$RG" -l "$LOCATION" -o none

# The last deploy's vault, if there was one (first run: none yet, so the secrets are made below).
KV=$(az deployment group show -g "$RG" -n warden --query properties.outputs.keyVault.value -o tsv 2>/dev/null || true)
secret() { [ -n "$KV" ] && az keyvault secret show --vault-name "$KV" -n "$1" --query value -o tsv 2>/dev/null || true; }

PG_PASSWORD=$(secret postgres-password)
[ -n "$PG_PASSWORD" ] || PG_PASSWORD=$(openssl rand -base64 32 | tr -dc 'A-Za-z0-9' | head -c 32)
PEM=$(secret signing-key-pem)
[ -n "$PEM" ] || PEM=$(openssl genpkey -algorithm RSA -pkeyopt rsa_keygen_bits:2048 2>/dev/null)
PEM_B64=$(printf '%s\n' "$PEM" | base64 | tr -d '\n')

deploy() {
  az deployment group create -g "$RG" -n warden -f main.bicep -o none -p \
    entraTenantId="$ENTRA_TENANT_ID" entraClientId="$ENTRA_CLIENT_ID" \
    wardenImage="$WARDEN_IMAGE" notesImage="$NOTES_IMAGE" \
    deployerObjectId="$DEPLOYER_OBJECT_ID" deployerType="$DEPLOYER_TYPE" \
    postgresPassword="$PG_PASSWORD" signingKeyPemBase64="$PEM_B64"
}
# The first time, Azure can take a minute to let Warden's new identity read Key Vault; one retry covers it.
deploy || { echo "retrying once (new Key Vault permissions can take a minute)…"; sleep 60; deploy; }

az deployment group show -g "$RG" -n warden --query "properties.outputs.{warden:wardenUrl.value, adminPage:adminPage.value, notes:notesInternalUrl.value}" -o table
