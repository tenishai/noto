# Argus - test layers per docs/testing.md, cheapest first.
# `make test` runs layers 1-3 (static, unit, contract). Layer 4 (smoke) is gated
# separately and lands with issue #24.

.PHONY: test lint unit contract smoke sync regen-golden validate

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

regen-golden:
	uv run python scripts/regen-golden.py

smoke:
	@echo "Layer 4 (kind smoke test) lands with issue #24 - the Phase 1 DoD gate."
	@exit 1
