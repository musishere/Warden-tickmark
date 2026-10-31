#!/usr/bin/env bash
# check-lock.sh — step 20, after a deploy: agent_container_cannot_reach_tools_directly, checked for real.
#
#   infra/check-lock.sh            (RESOURCE_GROUP defaults to warden; needs `az login`)
#
# Passes when: Warden answers on its public address, the notes tool server is set to internal-only
# ingress, and from outside Azure its address gets no answer from it (Azure's front door 404s). (That it refuses calls without
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
# The environment's wildcard DNS resolves every name to its shared front door, which answers 404 for
# an app with no public route; the notes server itself would answer 401 (no token). 000 = no answer.
check "notes can't be reached from here: $notes" \
  'case "$(curl -s -o /dev/null -w "%{http_code}" --max-time 15 -X POST "$notes")" in 000|404) true;; *) false;; esac'
exit $fail
