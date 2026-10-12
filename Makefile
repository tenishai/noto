# Noto - test layers per docs/testing.md, cheapest first.
# `make test` runs layers 1-3 (static, unit, contract). Layer 4 (smoke) is gated
# separately and lands with issue #24.

.PHONY: test lint unit contract smoke sync regen-golden validate validate-helm

sync:
	uv sync --frozen

lint:
	./scripts/lint-conventions.sh
	uv run ruff check .
	uv run ruff format --check .

unit: sync
	uv run pytest tests/unit

contract: sync
	uv run pytest tests/contract -m contract

test: lint unit contract

validate:
	bash scripts/validate-rendered.sh

validate-helm:
	bash scripts/validate-helm.sh

regen-golden:
	uv run python scripts/regen-golden.py

smoke:
	bash tests/smoke/run-smoke.sh
