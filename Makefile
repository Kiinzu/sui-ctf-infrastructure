.PHONY: build build-orchestrator build-instance up down logs ps clean test local-test

# Build both images. The instance image is under a compose profile, so build it explicitly.
build: build-orchestrator build-instance

build-orchestrator:
	docker compose build orchestrator

build-instance:
	docker compose --profile build-only build sui-instance

up:
	docker compose up -d orchestrator
	@echo "orchestrator up. nc <host> 1337 ; rpc proxy on :8080"

down:
	docker compose down

logs:
	docker compose logs -f orchestrator

ps:
	@echo "== orchestrator =="; docker compose ps
	@echo "== instances =="; docker ps --filter label=suictf --format 'table {{.Names}}\t{{.Status}}'

# Remove any leftover per-player instance containers (not managed by compose).
clean:
	-docker ps -aq --filter label=suictf | xargs -r docker rm -f

# Pure-Python unit tests (no Docker/Sui needed).
test:
	tmp/venv/bin/python -m pytest -q tests || python3 -m pytest -q tests

# End-to-end pipeline test using a LOCAL `sui` CLI (no Docker). See scripts/.
local-test:
	bash scripts/local_pipeline_test.sh
