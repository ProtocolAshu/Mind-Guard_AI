# MindGuard developer commands. Requires Python 3.12 and Node 22; PostgreSQL and Redis are optional for local work.
PYTHON ?= python3
PIP ?= $(PYTHON) -m pip
PYTHONPATH_ALL := backend:.
TEST_DATABASE_URL ?=
TEST_REDIS_URL ?=

.PHONY: help install install-web lint typecheck security test test-postgres test-all coverage migrate migration \
        api web e2e demo demo-rules seed docs train evaluate simulate experiments research android-verify \
        docker-up docker-down clean

help: ## Show the available commands
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "  %-16s %s\n", $$1, $$2}'

install: ## Install backend dependencies (runtime + development)
	$(PIP) install -r backend/requirements-dev.txt

install-web: ## Install dashboard dependencies
	cd web && npm ci

lint: ## Ruff (backend)
	cd backend && ruff check app tests

typecheck: ## mypy (backend) and tsc (web)
	cd backend && mypy app
	cd web && npm run typecheck

security: ## Bandit static analysis
	cd backend && bandit -q -c pyproject.toml -r app

test: ## Backend tests on SQLite plus web unit tests
	cd backend && pytest -q
	cd web && npm test

test-postgres: ## Backend tests against PostgreSQL+pgvector and Redis (set TEST_DATABASE_URL, TEST_REDIS_URL)
	cd backend && TEST_DATABASE_URL="$(TEST_DATABASE_URL)" TEST_REDIS_URL="$(TEST_REDIS_URL)" pytest -q

test-all: lint typecheck security test ## Everything that runs without external services

coverage: ## Backend coverage report
	cd backend && pytest -q --cov=app --cov-report=term-missing

migrate: ## Apply database migrations (uses DATABASE_URL)
	cd backend && alembic upgrade head

migration: ## Create a migration: make migration M="add table"
	cd backend && alembic revision --autogenerate -m "$(M)"

api: ## Run the API with reload
	cd backend && uvicorn app.main:app --reload --port 8000

web: ## Run the dashboard
	cd web && npm run dev

e2e: ## Start API + dashboard and run the backend-for-frontend flow check
	./scripts/run_e2e.sh

demo: ## Six demo scenarios against the real agent graph (served models)
	PYTHONPATH=$(PYTHONPATH_ALL) $(PYTHON) demo/run_demo.py

demo-rules: ## Same demos with the interpretable rules only
	PYTHONPATH=$(PYTHONPATH_ALL) $(PYTHON) demo/run_demo.py --rules-only

seed: ## Seed synthetic users for the admin console
	PYTHONPATH=$(PYTHONPATH_ALL) $(PYTHON) scripts/seed_synthetic.py --days 3

docs: ## Regenerate the API and configuration reference from the code
	PYTHONPATH=backend $(PYTHON) scripts/generate_docs.py

train: ## Generate the synthetic dataset and train the risk and content models
	PYTHONPATH=$(PYTHONPATH_ALL) $(PYTHON) -m simulator generate
	PYTHONPATH=$(PYTHONPATH_ALL) $(PYTHON) -m ml.training.train_risk
	PYTHONPATH=$(PYTHONPATH_ALL) $(PYTHON) -m ml.training.train_content

evaluate: ## Evaluate the registered models on the held-out split
	PYTHONPATH=$(PYTHONPATH_ALL) $(PYTHON) -m ml.evaluation.evaluate

simulate: ## Single simulator episode (quick check)
	PYTHONPATH=$(PYTHONPATH_ALL) $(PYTHON) -m simulator episode

experiments: ## Full experiment grid (resumable; rerun until it reports 0 remaining, then analyse)
	PYTHONPATH=$(PYTHONPATH_ALL) $(PYTHON) -m research.run_experiments --budget-seconds 900
	PYTHONPATH=$(PYTHONPATH_ALL) $(PYTHON) -m research.run_experiments --analyse

research: experiments ## Experiments plus the agent/system evaluation
	PYTHONPATH=$(PYTHONPATH_ALL) $(PYTHON) -m research.agent_eval

android-verify: ## Offline Kotlin type check and domain unit tests (no Gradle needed)
	KOTLINC=$${KOTLINC:-kotlinc} ANDROID_JAR=$${ANDROID_JAR:?set ANDROID_JAR} COROUTINES_JAR=$${COROUTINES_JAR:?set COROUTINES_JAR} \
	  android/tools/offline-check/verify.sh

docker-up: ## Start the full stack (PostgreSQL, Redis, API, dashboard)
	docker compose up --build

docker-down: ## Stop the stack and remove volumes
	docker compose down -v

clean: ## Remove caches and build output
	rm -rf backend/.pytest_cache backend/.ruff_cache backend/.mypy_cache web/.next web/node_modules
	find . -name __pycache__ -type d -prune -exec rm -rf {} +
