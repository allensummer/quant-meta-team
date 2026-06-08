PY ?= .venv/bin/python
PIP ?= .venv/bin/pip
DATA_DIR ?= $(shell .venv/bin/python -c "from quant_data.paths import data_dir; print(data_dir())" 2>/dev/null)

.PHONY: init sync-full sync-daily test report clean help \
        run-scheduler run-once run-once-dry-run

help:
	@echo "Targets:"
	@echo "  init              - install deps + create data dir + bootstrap duckdb + views"
	@echo "  sync-full         - backfill 5 tushare tables for the full A-share history"
	@echo "  sync-daily        - incremental daily sync (today - lookback)"
	@echo "  run-once          - single 5-table sweep (used by launchd / manual cron)"
	@echo "  run-once-dry-run  - dry-run of run-once (no network)"
	@echo "  run-scheduler     - in-process APScheduler 17:30 weekday trigger"
	@echo "  test              - run pytest with coverage"
	@echo "  report            - print row counts + sync_state + last lineage entries"
	@echo "  clean             - remove generated artifacts (parquet/duckdb/sqlite)"

init:
	$(PIP) install -e ".[dev]"
	$(PY) -m quant_data.cli init

sync-full:
	$(PY) -m quant_data.cli sync-full

sync-daily:
	$(PY) -m quant_data.cli sync-daily

run-once:
	$(PY) -m quant_data.cli run-once --lookback 1

run-once-dry-run:
	$(PY) -m quant_data.cli run-once --dry-run

run-scheduler:
	$(PY) -m quant_data.cli serve-scheduler --hour 17 --minute 30 --day-of-week mon-fri

test:
	$(PY) -m pytest tests/ --cov=quant_data --cov-report=term-missing --cov-fail-under=80

report:
	$(PY) -m quant_data.cli report

clean:
	rm -rf quant_data/data/raw_tushare_*
	rm -f quant_data/data/quant.duckdb
	rm -rf quant_data/data/meta
