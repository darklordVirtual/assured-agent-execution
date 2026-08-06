# Assured Agent Execution
#
#   make up          verify the pin, build, migrate, start, wait healthy
#   make scenarios   run the four decisions end to end against the running stack
#   make verify      pin + compatibility + e2e
#   make down        stop and remove everything, including volumes
#
# Every target that touches the core verifies the pin first. A pin nobody
# checks is a comment, not a control.

SHELL := /bin/sh
COMPOSE := docker compose
PY ?= python
VENV := .venv
VENV_PY := $(VENV)/bin/python
ifeq ($(OS),Windows_NT)
VENV_PY := $(VENV)/Scripts/python.exe
endif

.DEFAULT_GOAL := help
.PHONY: help pin env sign build up down logs ps verify compat e2e scenarios doctor clean

help:  ## Show this help
	@grep -hE '^[a-z-]+:.*?##' $(MAKEFILE_LIST) \
	  | awk 'BEGIN{FS=":.*?## "}{printf "  \033[36m%-12s\033[0m %s\n", $$1, $$2}'

pin:  ## Download and hash-verify the pinned REMORA core artifacts
	$(PY) scripts/verify_core_pin.py --out dist

env:  ## Generate this installation's secrets into .env (never overwrites)
	@$(PY) scripts/bootstrap_env.py

$(VENV_PY):
	$(PY) -m venv $(VENV)

deps: $(VENV_PY) pin  ## Install the SDK from the verified wheel, plus test deps
	$(VENV_PY) -m pip install --quiet --upgrade pip
	$(VENV_PY) -m pip install --quiet pytest "psycopg[binary]" \
	  "$$(ls dist/remora-*.whl)[sdk]"

sign: deps  ## Sign the ToolSpec bundle and pin its digest into .env
	$(VENV_PY) scripts/sign_toolpack.py --pin-into .env

check-sign: deps  ## Verify the signed bundle without re-signing it
	$(VENV_PY) scripts/sign_toolpack.py --check

build: pin  ## Build the control-plane image from the verified wheel
	$(COMPOSE) build

up: env sign build  ## Sign, build and bring the whole stack up
	$(COMPOSE) up -d
	@echo "waiting for the control plane to report healthy..."
	@i=0; until [ "$$($(COMPOSE) ps --format '{{.Health}}' control-plane)" = "healthy" ]; do \
	  i=$$((i+1)); \
	  if [ $$i -gt 60 ]; then \
	    echo "control plane did not become healthy; last 40 lines:"; \
	    $(COMPOSE) logs --tail 40 control-plane; exit 1; \
	  fi; sleep 2; \
	done
	@echo ""
	@echo "  Assured Agent Execution is up."
	@echo "  API      http://localhost:$${AAE_API_PORT:-8080}"
	@echo "  OpenAPI  http://localhost:$${AAE_API_PORT:-8080}/openapi.json"
	@echo ""
	@echo "  next:  make scenarios"

down:  ## Stop everything and remove volumes
	$(COMPOSE) down -v

logs:  ## Follow the control plane log
	$(COMPOSE) logs -f control-plane

ps:  ## Show what is running
	$(COMPOSE) ps

compat: deps  ## Pinned-core contract tests (no Docker needed)
	$(VENV_PY) -m pytest tests/compatibility -q

e2e: deps  ## End-to-end tests against the running stack
	$(VENV_PY) -m pytest tests/e2e -q

verify: compat e2e  ## Everything: pin, contract, end to end

scenarios: deps  ## Run ACCEPT, VERIFY, ABSTAIN and ESCALATE against the stack
	$(VENV_PY) -m aae.cli scenarios

doctor: deps  ## What is running, which core is pinned, what is configured
	$(VENV_PY) -m aae.cli doctor

clean:  ## Remove the venv and downloaded artifacts (keeps .env)
	rm -rf $(VENV) dist .pytest_cache
