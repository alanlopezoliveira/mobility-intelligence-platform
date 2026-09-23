.PHONY: install migrate prepare-data train evaluate test lint typecheck clean-data

install:
	python -m pip install -U pip
	python -m pip install -e .

	migrate:
	alembic upgrade head

prepare-data:
	alembic upgrade head
	python -m src.cli.main prepare-data

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
