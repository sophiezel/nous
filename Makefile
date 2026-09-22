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

# ── 光伏玻璃产业链跟踪 ─────────────────────────────────────────────
.PHONY: pv-seed pv-fetch pv-daily pv-signal pv-digest

pv-seed:
	$(VENV)/bin/nous pv seed

pv-fetch:
	$(VENV)/bin/nous pv fetch

# 日常例行：基线 + 抓取 + 信号（然后再跑 pv-digest 出周报）
pv-daily: pv-seed pv-fetch
	$(VENV)/bin/nous pv signal

pv-signal:
	$(VENV)/bin/nous pv signal

pv-digest:
	$(VENV)/bin/nous pv digest

.PHONY: check
check: lint test
	@echo "✓ All checks passed"
