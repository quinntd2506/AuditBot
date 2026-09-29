# AuditBot AI

An agentic smart-contract audit platform that connects to live contest platforms (Sherlock, Guardian), gets contest repositories, and uses AI to assist with vulnerability discovery and report generation.

---

## 🏗 Modular Architecture

The project has a containerized architecture:

- **Frontend**: React 19 + Vite (Modern, fast, glassmorphic UI).
- **Backend (API)**: FastAPI (Python) - Handles requests, database access, and job dispatching.
- **Worker**: Python Background Worker - Handles syncing platforms and fetching responses, triggers Trufy scans, and manages PoC generation jobs.
- **Sandbox**: Isolated Docker environment - Safely executes generated Proof-of-Concept (PoC) scripts to verify vulnerabilities without risking the host system.
- **Database**: PostgreSQL - Stores contest data, findings, and asynchronous job states.
- **Cache**: Redis - Manages the job queue and provides distributed locking for workspace safety.

---

## 📂 Project Structure

```
Contest-Bot/
│
├── frontend/               # React frontend (Vite SPA)
│   ├── src/
│   │   ├── context/        # DataContext (Global State & API hooks)
│   │   ├── components/     # UI Components (ContestTable, Modals, etc.)
│   │   ├── pages/          # Dashboard & ContestDetail pages
│   │   └── types.ts        # Shared TypeScript interfaces
│   └── ...
│
├── backend/                # API & Worker (Dockerized)
│   ├── api/                # FastAPI application
│   │   ├── main.py         # Entry point — mounts routers & startup logic
│   │   └── db.py           # PostgreSQL connection pool & Redis client
│   ├── worker/             # Agentic & Sync Engine
│   │   ├── worker.py       # Main job dispatcher
│   │   ├── trufy/          # Scanner orchestration & Batch Job logic
│   │   ├── verifier/       # Agentic PoC generation (LLM logic)
│   │   ├── shared/         # Shared DB & Config utilities
│   │   ├── guardian/       # Guardian Defender Bot logic
│   │   ├── sherlock/       # Sherlock Audits Bot logic
│   │   ├── code4rena/      # Code4rena Bot logic
│   │   ├── cantina/        # Cantina Bot logic
│   │   └── immunefi/       # Immunefi Bot logic
│   ├── .codex/             # Runtime data (git-ignored)
│   │   ├── skills/         # Skill .md files for LLM tools
│   │   └── uploads/        # Manually uploaded contest files
│   ├── init_db.py          # Database initialization & schema sync
│   ├── requirements.txt    # API & Worker dependencies
│   ├── Dockerfile.api      # API container
│   ├── Dockerfile.worker   # Main agent container
│   ├── Dockerfile.sandbox  # Isolated execution environment
│   └── docker-compose.yml  # Main infrastructure orchestration
│
├── makefile                # Local development automation
└── .env                    # Global configuration
```

---

## 💾 Database Access

Database is managed with **PostgreSQL**. Data is persisted in a Docker volume named `auditbot_pg_data`.

To inspect the database manually:
```bash
# Enter the Postgres interactive console
docker exec -it auditbot-postgres psql -U postgres -d auditbot

# Example queries:
SELECT * FROM contests;
SELECT * FROM notifications WHERE is_read = 0;
```

---

## 🛠 Development Workflow

Run the entire platform either fully containerized (Docker) or in a "Hybrid" mode where stateful services (Postgres, Redis) run in Docker while the API, Worker, and Frontend run natively on host machine for faster iteration.

### 🚀 Local Development (For local testing)

1.  **Configure Environment**:
    ```bash
    cp .env.example .env
    ```
2.  **Initialize Environment**:
    ```bash
    make setup   # Creates venv, installs dependencies for backend & frontend
    ```
3.  **Start Databases**:
    ```bash
    make db-up   # Starts Postgres & Redis containers
    make db-init # Initializes the DB schema
    ```
4.  **Run Services**:
    - **All-in-one**: `make dev` (Runs API, Worker, and Frontend in one terminal).
    - **Separate terminals** (Better for logs):
        - `make api`
        - `make worker`
        - `make frontend`
    
    Once the services are running, access them via:
    - **Frontend Dashboard**: [http://localhost:5173](http://localhost:5173)
    - **Backend API Swagger Docs**: [http://localhost:8001/docs](http://localhost:8001/docs)

5. **Stopping Services**:
    - **Docker Services**: Run `make db-down` to stop and remove containers.
    - **Local Processes**: Go to the terminal where `make dev` (or `make frontend/api/worker`) is running and press `Ctrl + C`.


### 🐳 Full Docker Mode (For production testing)
```bash
cd backend && docker compose up -d --build
cd ../frontend && docker compose up -d --build
```

---

## 🔍 Logs & Debugging
- **Hybrid Mode**: If using `make dev`, logs are interleaved. For isolated debugging, run services in separate terminals (`make api`, `make worker`, etc.).
- **Docker Mode**:
  ```bash
  docker compose logs -f api
  docker compose logs -f worker
  ```

---

## 🏗 Repository Download Logic

The agentic verification pipeline is optimized to minimize GitHub bandwidth and disk usage:

### 1. Auto Mode (Autonomous Loop)
- **Shared Workspace**: The worker identifies a contest, downloads the zip **once**, and extracts it to `/tmp/workspace_{contest_id}`.
- **Batch Processing**: It iterates through all findings for that contest sequentially using the same folder.
- **Cleanup**: The folder is deleted only after the entire contest audit cycle is complete.

### 2. Review Mode (Manual Verification)
- **Batch Jobs**: When multiple issues are selected in the UI for "Verify Selected", a background job is created.
- **Deduplication**: Issues are grouped by `contest_id`. The worker downloads the repo **once** for the entire group, even if they belong to different issues.
- **Concurrency Control**: A Redis-based lock (`lock:workspace:{contest_id}`) ensures that if two different jobs or workers try to verify the same contest simultaneously, they don't corrupt the workspace or waste bandwidth.
- **Isolation**: Each batch job uses a unique path `/tmp/batchjob_{job_id}_{contest_id}` to avoid interference.

---

## 🛡 Platform Authentication

### Guardian Defender
- Uses automatic email/password login.
- JWT is cached locally to `.codex/guardian_token.json`.
- **Finding Sync**: Automatically pulls historical submissions. Use **Force Ping** for real-time updates.

### Sherlock Audits
- Contest **listing** is public.
- Authenticated actions use the `SHERLOCK_SESSION_COOKIE` provided in `.env`.
- Ensure the cookie is refreshed if authentication errors appear in the logs.
- **Private Repository Support**: Sherlock provisions private repositories based on the connected GitHub handle (e.g., `sherlock-audit/<contest>-<github_handle>`). 
- **GitHub PAT**: To allow Trufy scanners to download Sherlock private code, `.env` must have a `GITHUB_TOKEN`. It is strictly required to use a **Classic PAT** (starting with `ghp_`) with the `repo` scope. Fine-Grained PATs will fail as they cannot automatically inherit permissions for newly invited repositories.

### Code4rena
- Uses `CODE4RENA_USERNAME` and `CODE4RENA_PASSWORD` for automated interactions.
- Authenticates with the platform's login endpoint to track submissions and judge responses.

### Trufy Audits
- Uses `TRUFY_EMAIL` and `TRUFY_PASSWORD` for authentication.
- Used to fetch static analysis findings before AI verification.

### Cantina
- Authenticates using `CANTINA_EMAIL` and `CANTINA_PASSWORD`, or optionally `CANTINA_AUTH_TOKEN`.
- The bot handles automated session persistence to track contests and findings.

### Immunefi
- Uses `IMMUNEFI_EMAIL` and `IMMUNEFI_PASSWORD` for authentication (via Firebase idTokens).
- **Contest Discovery**: Leverages community-maintained JSON snapshots and falls back to dynamic React Server Component (RSC) parsing via Next.js inline scripts.
- **Finding Sync**: Directly queries internal APIs (`/api/me/submitted-reports`) to fetch judge verdicts and updates the local database dynamically.
- *Note:* Due to KYC and legal constraints, automated "Auto-Join" is intentionally disabled for Immunefi. Users must manually enroll in contests on the platform.
---

## 🏆 Contest Management

The AuditBot is designed to not miss an audit while giving the user full control over their participation.

- **Automatic Enrollment**: By default, the bot **auto-joins** any contest as soon as it transitions to "Active" status. This behavior is standard across all integrated platforms (Guardian, Sherlock, etc.).
- **Smart Sync**: Because platform APIs often hide enrollment data from automated scripts, the bot uses a **"Sticky Enrollment"** record. Once the bot joins a contest (or the user manually click join), it "remembers" that the user is enrolled until the user chooses otherwise.
- **Manual Overrides**:
  - **Join Now**: The user can manually trigger a join for "Upcoming" contests from the Dashboard to get in early.
  - **Withdraw Bot**: If the user wishes to skip a specific contest, use the "Withdraw Bot" button in the Contest Details. This sets a `skip_autojoin` flag, and the bot will **permanently ignore** that contest until the user re-enrolls.
