.PHONY: install migrate prepare-data build-forecasting-contract benchmark train evaluate test lint typecheck clean-data

install:
	python -m pip install -U pip
	python -m pip install -e ".[dev]"

migrate:
	alembic upgrade head

prepare-data:
	alembic upgrade head
	python -m src.cli.main prepare-data

build-forecasting-contract:
	python scripts/build_forecasting_contract.py

benchmark:
	python -m src.ml.benchmark

train:
	python -m src.cli.main train

evaluate:
	python -m src.cli.main evaluate

test:
	pytest -q

lint:
	ruff check src tests

typecheck:
	mypy src

clean-data:
	rm -rf ./data/bronze ./data/silver ./data/gold ./models/production ./models/experiments
rebuild-project:
	python scripts/rebuild_project.py

web-project:
	cd frontend && npm run dev -- --host 127.0.0.1 --port 5177
