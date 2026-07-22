.PHONY: install install-dev test lint clean serve acceptance

VENV := .venv
PYTHON := $(VENV)/bin/python
PIP := $(VENV)/bin/pip
PYTEST := $(VENV)/bin/pytest
RUFF := $(VENV)/bin/ruff

install: $(VENV)
	$(PIP) install -e .

install-dev: $(VENV)
	$(PIP) install -e ".[dev,api,scheduler,ml,trading,backtest]"

acceptance:
	$(VENV)/bin/nous accept

# 开发机快捷：与 nous accept 等价
accept: acceptance
.PHONY: accept

$(VENV):
	python3 -m venv $(VENV)
	$(PIP) install --upgrade pip

test:
	$(PYTEST) tests/ -v

test-cov:
	$(PYTEST) tests/ -v --cov=nous --cov-report=term-missing

lint:
	$(RUFF) check src/nous/ tests/

lint-fix:
	$(RUFF) check --fix src/nous/ tests/

clean:
	rm -rf $(VENV)
	rm -rf *.egg-info
	rm -rf __pycache__ nous/__pycache__
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true

serve:
	$(PYTHON) -m nous.cli serve --reload

.PHONY: check
check: lint test
	@echo "✓ All checks passed"
