.PHONY: hooks check

hooks:
	uvx pre-commit install

check:
	uvx ruff==0.15.16 check .
	uvx ruff==0.15.16 format --check .
	python3 -m compileall -q demos templates
