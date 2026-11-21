PYTHON ?= python3
VENV ?= .venv
PROFILE ?= cores3
DEVICE ?= cores3-01
ENV_FILE ?= .env
GATEWAY_URL ?= http://127.0.0.1:8787
PY := $(VENV)/bin/python
CLIK := $(VENV)/bin/clikvm

.PHONY: setup init init-demo serve simulate test test-python test-desktop test-native standards firmware package

setup:
	$(PYTHON) -m venv $(VENV)
	$(PY) -m pip install -r requirements.lock
	$(PY) -m pip install --no-deps -e '.[dev]'
	$(PY) -m pip install -e 'agents/desktop[rfb]'

init:
	$(CLIK) init-env --output $(ENV_FILE) --device $(DEVICE)

init-demo:
	$(CLIK) init-env --output $(ENV_FILE) --device $(DEVICE) --simulation

serve:
	$(CLIK) --env-file $(ENV_FILE) serve --host 127.0.0.1 --port 8787

simulate:
	$(CLIK) --env-file $(ENV_FILE) simulate --url $(GATEWAY_URL) --device $(DEVICE)

test: test-python test-desktop test-native standards

test-python:
	PYTHONPATH=gateway $(PY) -m unittest discover -s tests -p 'test_gateway*.py' -v
	PYTHONPATH=gateway $(PY) -m unittest discover -s gateway/tests -p 'test_audit.py' -v

test-native:
	ASAN_OPTIONS=detect_leaks=0 sh tests/native/run.sh

test-desktop:
	PYTHONPATH=gateway:agents/desktop $(PY) -m unittest discover -s tests/desktop -v

standards:
	$(PY) scripts/standards.py check

firmware:
	bash scripts/firmware.sh $(PROFILE) build

package:
	$(PY) scripts/package_release.py --output dist
