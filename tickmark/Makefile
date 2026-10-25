# Tickmark. The frontend is served by the backend: `make run`, then open http://localhost:8000
PY := .venv/bin/python
PORT ?= 8000

.PHONY: help setup db migrate run certs run-https logs demo warden-demo agent test eval eval-real eval-variety stop

help:
	@echo "make setup    create .venv and install (first time)"
	@echo "make db       start Postgres (docker) and apply migrations"
	@echo "make run      start backend + frontend on http://localhost:$(PORT) (auto-reload)"
	@echo "make certs    once: trusted https certificate for the Excel add-in"
	@echo "make run-https  backend over https for the Excel add-in (docs/how-it-works.md, section 15)"
	@echo "make logs     follow the step-by-step log"
	@echo "make demo     generate the easy and tough test cases in data/demo/"
	@echo "make warden-demo  files for Warden's demo engagements (Coke, Pepsi) behind the tools"
	@echo "make agent TOKEN=... ENG=...  vouch an engagement through Warden (TOKEN: dev-token sara tickmark)"
	@echo "make test     run the tests"
	@echo "make eval     verdicts vs labels on the test sets, own text only (no Azure)"
	@echo "make stop     stop Postgres"

setup:
	uv venv --python 3.12 .venv
	uv pip install --python $(PY) -e ".[dev]"
	cp -n .env.example .env || true

db:
	docker compose up -d --wait
	.venv/bin/alembic upgrade head

migrate:
	.venv/bin/alembic upgrade head

run:
	.venv/bin/uvicorn tickmark.api.main:app --reload --port $(PORT)

CERTS := $(HOME)/.office-addin-dev-certs
certs:  # once: a localhost certificate Excel trusts (asks for your password to add it to the keychain)
	npx --yes office-addin-dev-certs install

run-https:  # the backend over https, as the Excel add-in needs (addin/manifest.xml)
	.venv/bin/uvicorn tickmark.api.main:app --reload --port $(PORT) \
		--ssl-keyfile $(CERTS)/localhost.key --ssl-certfile $(CERTS)/localhost.crt

logs:
	tail -f data/logs/tickmark.log

demo:
	$(PY) -m tools.datagen.generate --count 20 --seed 1 --messy
	$(PY) -m tools.datagen.hard
	$(PY) -m tools.datagen.demo

# Warden's demo engagement ids (warden/src/Warden.Api/Data/DemoSeed.cs): Coke and Pepsi
COKE := e0000000-0000-0000-0000-000000000001
PEPSI := e0000000-0000-0000-0000-000000000002
warden-demo:
	$(PY) -c "from pathlib import Path; from tools.datagen.demo import easy; [easy(Path('data/engagements', e)) for e in ('$(COKE)', '$(PEPSI)')]"

agent:
	$(PY) -m tickmark.agent --token $(TOKEN) --engagement $(or $(ENG),$(COKE)) --period 2025-01-01:2025-12-31 --currency EUR

test:
	.venv/bin/pytest -q

eval:
	$(PY) -m tools.eval_vouch --azure off --no-line-items

eval-real:  # real invoices vs the hand-checked truth; Azure results come from data/cache/docint
	$(PY) -m tools.eval_vouch --azure auto --no-line-items real

eval-variety:  # unseen kinds: image invoices, other layouts, scans, phone photos (data/real/variety)
	$(PY) -m tools.eval_vouch --azure auto --no-line-items variety-originals variety-scans variety-photos

stop:
	docker compose down
