.PHONY: hooks catalog catalog-check

hooks: ## install the repo's git hooks (run once per checkout)
	git config core.hooksPath .githooks
	@echo "hooks installed; demos changes will regenerate catalog.json on commit."

catalog: ## regenerate catalog.json from demos/*/playground.json
	python3 scripts/build_catalog.py

catalog-check: ## fail if catalog.json is stale
	python3 scripts/build_catalog.py --check
