.PHONY: hooks check demo

hooks:
	uvx pre-commit install

check:
	uvx ruff==0.15.16 check .
	uvx ruff==0.15.16 format --check .
	python3 -m compileall -q demos templates

# Check one example before a pull request: make demo DEMO=demos/your-app
demo:
	python3 scripts/check_demo.py $(DEMO)
