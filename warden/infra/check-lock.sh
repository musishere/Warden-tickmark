#!/usr/bin/env bash
# check-lock.sh — step 20, after a deploy: agent_container_cannot_reach_tools_directly, checked for real.
#
#   infra/check-lock.sh            (RESOURCE_GROUP defaults to warden; needs `az login`)
#
# Passes when: Warden answers on its public address, the notes tool server is set to internal-only
# ingress, and its address doesn't even resolve from outside Azure. (That it refuses calls without
# Warden's token is the server's own check, tested locally: examples/notes-server answers 401.)
set -uo pipefail
RG=${RESOURCE_GROUP:-warden}
fail=0
check() { if eval "$2"; then echo "✅ $1"; else echo "❌ $1"; fail=1; fi; }

domain=$(az containerapp env show -g "$RG" -n warden-env --query properties.defaultDomain -o tsv)
warden="https://warden.$domain"
notes="https://notes.internal.$domain/mcp"

check "Warden is public: $warden/health" \
  '[ "$(curl -s -o /dev/null -w "%{http_code}" --max-time 60 "$warden/health")" = 200 ]'
check "notes ingress is internal only" \
  '[ "$(az containerapp show -g "$RG" -n notes --query properties.configuration.ingress.external -o tsv)" = false ]'
check "notes can't be reached from here: $notes" \
  '! curl -s -o /dev/null --max-time 15 -X POST "$notes"'
exit $fail
