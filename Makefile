.PHONY: hooks catalog catalog-check

hooks: ## install pre-commit (run once per checkout)
	uvx pre-commit install
	@echo "pre-commit installed; ruff, formatting, and catalog regen run on commit."

catalog: ## regenerate catalog.json from demos/*/playground.json
	python3 scripts/build_catalog.py

catalog-check: ## fail if catalog.json is stale
	python3 scripts/build_catalog.py --check
