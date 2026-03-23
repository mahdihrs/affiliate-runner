VENV := .venv
PYTHON := $(VENV)/bin/python3
PIP := $(VENV)/bin/pip

.PHONY: install seed run test-pipeline test-caption test-fetch seed-queue help

help:
	@echo "Available commands:"
	@echo "  make install        Install all dependencies + Playwright browsers"
	@echo "  make seed           Seed niches and adlibs to Supabase"
	@echo "  make run            Start the scheduler (all 4 jobs)"
	@echo "  make test-pipeline  Run a single pipeline slot manually"
	@echo "  make test-fetch     Test Playwright fetcher for a niche"
	@echo "  make test-caption   Test Claude caption generator"
	@echo "  make seed-queue     Manually add a product to post_queue (interactive)"

seed-queue:
	PYTHONPATH=. $(PYTHON) scripts/seed_queue.py

install:
	python3.11 -m venv $(VENV)
	$(PIP) install -r requirements.txt
	$(VENV)/bin/playwright install chromium

seed:
	$(PYTHON) scripts/seed.py

run:
	$(PYTHON) main.py

test-pipeline:
	PYTHONPATH=. $(PYTHON) scripts/test_pipeline.py

test-fetch:
	PYTHONPATH=. $(PYTHON) scripts/test_fetch.py

test-caption:
	PYTHONPATH=. $(PYTHON) scripts/test_caption.py
