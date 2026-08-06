# Assured Agent Execution
#
# Every target delegates to run.py, so there is exactly one implementation.
# That is not tidiness: a shell version and a Python version drift, and the
# first person to notice is a new developer following a walkthrough that no
# longer matches what runs.
#
# `make` is not installed on a default Windows machine. If you do not have it:
#
#     python run.py up
#     python run.py scenarios
#
# is the same thing, and is what the documentation uses.

PY ?= python

.DEFAULT_GOAL := help
.PHONY: help up down build pin env deps sign check-sign verify compat e2e \
        scenarios doctor logs ps clean

help:  ## Show this help
	@grep -hE '^[a-z-]+:.*?##' $(MAKEFILE_LIST) \
	  | awk 'BEGIN{FS=":.*?## "}{printf "  \033[36m%-12s\033[0m %s\n", $$1, $$2}'

up:  ## Verify the pin, generate secrets, sign, build, migrate, start
	$(PY) run.py up

down:  ## Stop everything and remove the volumes
	$(PY) run.py down

build:  ## Build both images from the verified wheel
	$(PY) run.py build

pin:  ## Download and hash-verify the pinned REMORA core artifacts
	$(PY) run.py pin

env:  ## Generate this installation's secrets (never overwrites)
	$(PY) run.py env

deps:  ## Create the venv and install the SDK from the verified wheel
	$(PY) run.py deps

sign:  ## Sign the ToolSpec bundle and pin its digest into .env
	$(PY) run.py sign

check-sign:  ## Verify the signed bundle without re-signing it
	$(PY) run.py check-sign

verify:  ## Everything: pin, contract tests, end-to-end tests
	$(PY) run.py verify

compat:  ## Pinned-core contract tests (no Docker needed)
	$(PY) run.py compat

e2e:  ## End-to-end tests against the running stack
	$(PY) run.py e2e

scenarios:  ## Run the four decisions against the running stack
	$(PY) run.py scenarios

doctor:  ## What is pinned, what is served, what is reachable
	$(PY) run.py doctor

logs:  ## Follow the control plane log
	$(PY) run.py logs

ps:  ## Show what is running
	$(PY) run.py ps

clean:  ## Remove the venv and downloaded artifacts (keeps .env)
	$(PY) run.py clean
