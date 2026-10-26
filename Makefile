.ONESHELL:
.PHONY: test run run_coverage report_coverage development-diff-cover uninstall build install setup deploy down link-cli

DYDX ?= 0
ENV_FILE := setup/environment.yml
PYTHON ?= python
CLOUDRUN_IMAGE ?= hummingbot-cloudrun:local
BUILD_JOBS ?= 2
CLOUDRUN_CONFIG ?= deploy/cloudrun/service.example.yaml
CLOUDRUN_REGION ?= asia-southeast1
CLOUDRUN_EVIDENCE ?= build/e2e-offline/result.json
OFFLINE_OKX_TRADING_ENV ?= demo
MM_LOAD_PROFILE ?= all
MM_LOAD_TRACE ?= false
ifeq ($(DYDX),1)
  ENV_FILE := setup/environment_dydx.yml
endif

.PHONY: swagger swagger-check
swagger:
	$(PYTHON) -m hummingbot.cli.swagger

swagger-check:
	$(PYTHON) -m hummingbot.cli.swagger --check

test:
	coverage run -m pytest \
	--ignore="test/integration" \
 	--ignore="test/mock" \
 	--ignore="test/hummingbot/connector/exchange/ndax/" \
 	--ignore="test/hummingbot/connector/derivative/dydx_v4_perpetual/" \
 	--ignore="test/connector/utilities/oms_connector/" \
 	--ignore="test/hummingbot/strategy/amm_arb/" \
 	--ignore="test/hummingbot/strategy/cross_exchange_market_making/" \

run_coverage: test
	coverage report
	coverage html

report_coverage:
	coverage report
	coverage html

development-diff-cover:
	coverage xml
	diff-cover --compare-branch=origin/development coverage.xml

build:
	docker build --target release --build-arg BUILD_JOBS=$(BUILD_JOBS) -t hummingbot/hummingbot${TAG} -f Dockerfile .

.PHONY: build-cloudrun test-cloudrun-regression test-e2e-offline review-cloudrun-config deploy-cloudrun
test-cloudrun-regression:
	set -eu
	$(PYTHON) -m pytest -q test/hummingbot/client/config
	$(PYTHON) -m pytest -q test/hummingbot/client/test_performance.py
	$(PYTHON) -m pytest -q test/hummingbot/cli test/hummingbot/model test/hummingbot/client/test_runner.py \
		test/hummingbot/connector/test_markets_recorder.py \
		test/hummingbot/connector/exchange/okx test/hummingbot/connector/derivative/okx_perpetual \
		test/hummingbot/core/test_trading_core.py test/hummingbot/core/web_assistant \
		test/hummingbot/strategy/test_strategy_v2_base.py \
		test/hummingbot/strategy_v2/executors/test_executor_orchestrator.py \
		test/hummingbot/strategy_v2/executors/test_mm_drawdown_reports.py \
		test/hummingbot/strategy_v2/executors/position_executor
	$(PYTHON) -m pytest -q test/hummingbot/connector/test_okx_environment.py \
		test/hummingbot/connector/test_okx_environment_transport.py \
		test/hummingbot/connector/test_okx_account_observations.py \
		test/hummingbot/core/test_mm_session.py test/hummingbot/core/test_admin_reconciliation.py test/hummingbot/core/test_funding_sync.py \
		test/hummingbot/core/test_spot_outflow.py \
		test/hummingbot/core/test_mm_liquidity.py \
		test/hummingbot/core/utils/test_read_retry.py \
		test/hummingbot/strategy_v2/controllers/test_life_mm.py \
		test/hummingbot/strategy_v2/controllers/test_quote_guard.py \
		test/hummingbot/strategy_v2/controllers/test_listing_bootstrap.py \
		test/hummingbot/strategy_v2/controllers/test_inventory_skew.py \
		test/hummingbot/strategy/pure_market_making/test_inventory_skew_calculator.py \
		test/hummingbot/strategy_v2/controllers/test_life_startup_availability.py \
		test/hummingbot/strategy_v2/controllers/test_reference_subscriptions.py \
		test/hummingbot/strategy_v2/controllers/test_controller_base.py \
		test/hummingbot/strategy_v2/controllers/test_controller_lifecycle.py \
		test/hummingbot/strategy_v2/test_runnable_base.py \
		test/hummingbot/strategy_v2/test_mm_drawdown.py \
		test/hummingbot/data_feed/test_market_data_provider.py test/hummingbot/data_feed/test_provider_shutdown.py

build-cloudrun:
	mkdir -p build
	docker buildx build --platform linux/amd64 --target cloudrun --load \
		--build-arg BUILD_JOBS=$(BUILD_JOBS) --metadata-file build/cloudrun-image.json \
		-t $(CLOUDRUN_IMAGE) -f Dockerfile .

test-e2e-offline:
	$(PYTHON) scripts/test_e2e_offline.py --image $(CLOUDRUN_IMAGE) --trading-env $(OFFLINE_OKX_TRADING_ENV) $(if $(CLOUDRUN_PREVIOUS_IMAGE),--previous-image $(CLOUDRUN_PREVIOUS_IMAGE),)

.PHONY: test-mm-load
test-mm-load:
	$(PYTHON) scripts/test_mm_load.py --image $(CLOUDRUN_IMAGE) --profile $(MM_LOAD_PROFILE) $(if $(filter true,$(MM_LOAD_TRACE)),--trace,)

review-cloudrun-config:
	$(PYTHON) scripts/review_cloudrun.py $(CLOUDRUN_CONFIG) --evidence $(CLOUDRUN_EVIDENCE)

deploy-cloudrun:
	set -eu
	$(PYTHON) scripts/review_cloudrun.py $(CLOUDRUN_CONFIG) --evidence $(CLOUDRUN_EVIDENCE)
	gcloud run services replace $(CLOUDRUN_CONFIG) --region $(CLOUDRUN_REGION)

.PHONY: migrate test-postgres
migrate:
	$(PYTHON) -m hummingbot.model.db_migration.postgres

test-postgres:
	$(PYTHON) scripts/test_postgres.py


uninstall:
	conda env remove -n hummingbot -y

install:
	@if ! command -v conda >/dev/null 2>&1; then \
		echo "Error: Conda is not found in PATH. Please install Conda or add it to your PATH."; \
		exit 1; \
	fi
	@mkdir -p logs
	@echo "Using env file: $(ENV_FILE)"
	@if conda env list | awk '{print $$1}' | grep -qx hummingbot; then \
		conda env update -n hummingbot -f "$(ENV_FILE)"; \
	else \
		conda env create -n hummingbot -f "$(ENV_FILE)"; \
	fi
	@if [ "$$(uname)" = "Darwin" ]; then \
		conda install -n hummingbot -y appnope; \
	fi
	@conda run -n hummingbot conda develop .
	@conda run -n hummingbot python -m pip install --no-deps -r setup/pip_packages.txt > logs/pip_install.log 2>&1
	@conda run -n hummingbot pre-commit install
	@if [ "$$(uname)" = "Linux" ] && command -v dpkg >/dev/null 2>&1; then \
		if ! dpkg -s build-essential >/dev/null 2>&1; then \
			echo "build-essential not found, installing..."; \
			sudo apt-get update && sudo apt-get upgrade -y && sudo apt-get install -y build-essential; \
		fi; \
	fi
	@conda run -n hummingbot --no-capture-output python setup.py build_ext --inplace
	@conda run -n hummingbot bash -c 'ln -sf "$(CURDIR)/bin/hbot" "$$CONDA_PREFIX/bin/hbot"'
	@echo "Done. Run: conda activate hummingbot && hbot --help"

link-cli:
	@src="$(CURDIR)/bin/hbot-host"; dir="$${HBOT_BIN:-}"; \
	if [ -z "$$dir" ]; then \
		for d in /usr/local/bin "$$HOME/.local/bin"; do \
			if [ -w "$$d" ] || { [ ! -e "$$d" ] && mkdir -p "$$d" 2>/dev/null; }; then dir="$$d"; break; fi; \
		done; \
	fi; \
	if [ -z "$$dir" ]; then \
		echo "No writable bin dir found (tried /usr/local/bin, ~/.local/bin)."; \
		echo "Set HBOT_BIN to a writable dir on your PATH and retry, e.g.  make link-cli HBOT_BIN=\$$HOME/.local/bin"; \
		exit 1; \
	fi; \
	mkdir -p "$$dir"; ln -sf "$$src" "$$dir/hbot"; \
	echo "Linked $$dir/hbot -> bin/hbot-host"; \
	case ":$$PATH:" in *":$$dir:"*) ;; *) echo "NOTE: add $$dir to your PATH to run 'hbot'." ;; esac; \
	echo "Now 'hbot <command>' dispatches to your source env or the docker container."

run:
	conda run -n hummingbot --no-capture-output ./bin/hummingbot_quickstart.py $(ARGS)

setup:
	@read -r -p "Include Gateway? [y/N] " ans; \
	if [ "$$ans" = "y" ] || [ "$$ans" = "Y" ]; then \
		echo "COMPOSE_PROFILES=gateway" > .compose.env; \
		echo "Gateway will be included."; \
	else \
		echo "COMPOSE_PROFILES=" > .compose.env; \
		echo "Gateway will NOT be included."; \
	fi

deploy:
	@if [ -f ./.compose.env ]; then set -a; . ./.compose.env; set +a; fi; \
	docker compose up -d

down:
	docker compose --profile gateway down
