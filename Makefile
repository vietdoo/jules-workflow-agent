.PHONY: install api web telegram dev test check

install:
	python -m pip install -r requirements.txt
	pnpm --dir apps/web install

api:
	python -m apps.api

web:
	pnpm --dir apps/web dev

telegram:
	python -m src.main

dev:
	python scripts/run_local.py

test:
	python -m unittest discover -s tests -v

check:
	python -m compileall -q src apps tests
	python -m unittest discover -s tests -v
	pnpm --dir apps/web lint
	pnpm --dir apps/web build
