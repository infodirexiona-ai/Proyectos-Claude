.PHONY: instalar navegador servir test lint demo

instalar:
	python3 -m venv .venv && .venv/bin/pip install -r requirements-dev.txt

navegador:
	.venv/bin/playwright install chromium

servir:
	.venv/bin/uvicorn app.web.main:app --reload --port 8000

demo:
	SII_MODO=demo .venv/bin/uvicorn app.web.main:app --port 8000

test:
	.venv/bin/python -m pytest -q

lint:
	.venv/bin/ruff check app tests
	.venv/bin/ruff format --check app tests
