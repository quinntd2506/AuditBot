.PHONY: setup setup-backend setup-frontend db-up db-init db-down api worker frontend dev docker-space docker-clean

BACKEND_DIR := backend
FRONTEND_DIR := frontend
BACKEND_VENV := $(BACKEND_DIR)/venv
BACKEND_STAMP := $(BACKEND_VENV)/.requirements-installed
PYTHON := $(BACKEND_VENV)/bin/python
PIP := $(BACKEND_VENV)/bin/pip
API_RELOAD ?=

# Load ../.env, then override service hostnames for local processes.
LOCAL_ENV = set -a; . ../.env; set +a; \
	export DATABASE_URL=postgresql://postgres:$${POSTGRES_PASSWORD}@localhost:5432/auditbot; \
	export REDIS_URL=redis://localhost:6381/0; \
	export UPLOAD_DIR=.codex/uploads; \
	export PYTHONPATH=.

setup: setup-backend setup-frontend
	@echo "Local environment ready. Run 'make db-up db-init', then use 'make dev' or separate terminals."

setup-backend: $(BACKEND_STAMP)

$(BACKEND_STAMP): $(BACKEND_DIR)/requirements.txt
	cd $(BACKEND_DIR) && /usr/bin/python3 -m venv venv
	cd $(BACKEND_DIR) && ./venv/bin/pip install -r requirements.txt
	touch $(BACKEND_STAMP)

setup-frontend:
	cd $(FRONTEND_DIR) && npm install

# Start only stateful infrastructure in Docker. This does not rebuild API/worker images.
db-up:
	cd $(BACKEND_DIR) && docker compose --env-file ../.env up -d postgres redis
	@echo "Databases running on localhost:5432 and localhost:6381"

db-init: $(BACKEND_STAMP)
	cd $(BACKEND_DIR) && $(LOCAL_ENV); ./venv/bin/python init_db.py

db-down:
	cd $(BACKEND_DIR) && docker compose down

api: $(BACKEND_STAMP)
	cd $(BACKEND_DIR) && mkdir -p .codex/uploads
	cd $(BACKEND_DIR) && $(LOCAL_ENV); ./venv/bin/python -m uvicorn api.main:app $(API_RELOAD) --host 127.0.0.1 --port 8001 --log-level warning

worker: $(BACKEND_STAMP)
	cd $(BACKEND_DIR) && $(LOCAL_ENV); ./venv/bin/python -m worker.worker

frontend:
	cd $(FRONTEND_DIR) && npm run dev -- --host 127.0.0.1

# Run API, worker, and frontend locally. Ctrl-C stops the foreground processes.
dev: db-up db-init
	$(MAKE) -j3 api worker frontend

# Cleanly stop all local processes and free up ports.
stop:
	-pkill -f "worker.worker"
	-pkill -f "uvicorn"
	-pkill -f "vite"
	-lsof -ti :8001,5173,5174 | xargs kill -9 2>/dev/null || true
	@echo "Processes stopped and ports cleared."

docker-space:
	docker system df

# Manual cleanup target for unused Docker build cache/images/containers.
docker-clean:
	docker system prune -f
