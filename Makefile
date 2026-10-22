.PHONY: install dev test lint eval run docker check

install:
	pip install -r requirements.txt

dev:
	pip install -r requirements-dev.txt

# One command that runs the main automated checks (tests + retrieval eval).
check: test eval

test:
	python -m pytest -p no:warnings

lint:
	ruff check app tests eval

eval:
	python -m eval.run_eval

run:
	uvicorn app.main:app --host 0.0.0.0 --port $${PORT:-8000} --reload

docker:
	docker compose up --build
