"""
Shared configuration — loads .env from project root.
Import this before any other shared module.
"""
import os
from pathlib import Path
from dotenv import load_dotenv

# backend/ is the base of the code, root is one level up
BACKEND_DIR = Path(__file__).resolve().parent.parent.parent
ROOT_DIR = BACKEND_DIR.parent

# Load .env from project root
env_path = ROOT_DIR / ".env"
if env_path.exists():
    load_dotenv(env_path)
else:
    print(f"⚠️ Warning: .env not found at {env_path}")

# ── Guardian ───────────────────────────────────────────────────────────────────
GUARDIAN_EMAIL: str = os.getenv("GUARDIAN_EMAIL", "")
GUARDIAN_PASSWORD: str = os.getenv("GUARDIAN_PASSWORD", "")

# ── Sherlock ───────────────────────────────────────────────────────────────────
# Fallback cookie string if Edge cookie extraction fails
SHERLOCK_SESSION_COOKIE: str = os.getenv("SHERLOCK_SESSION_COOKIE", "")

# ── Code4rena ───────────────────────────────────────────────────────────────────
CODE4RENA_USERNAME: str = os.getenv("CODE4RENA_USERNAME", "")
CODE4RENA_PASSWORD: str = os.getenv("CODE4RENA_PASSWORD", "")

# ── Trufy ────────────────────────────────────────────────────────────────────────
TRUFY_EMAIL: str = os.getenv("TRUFY_EMAIL", "") or os.getenv("TRUFY_EMAIl", "")  # handle typo in .env
TRUFY_PASSWORD: str = os.getenv("TRUFY_PASSWORD", "")
TRUFY_API: str = "https://api.trufy.io"

# ── Immunefi ─────────────────────────────────────────────────────────────────────
# Credentials used ONLY for authenticated actions (join, check responses).
# Contest discovery is fully public and uses no credentials.
IMMUNEFI_EMAIL: str = os.getenv("IMMUNEFI_EMAIL", "")
IMMUNEFI_PASSWORD: str = os.getenv("IMMUNEFI_PASSWORD", "")

# ── GitHub ─────────────────────────────────────────────────────────────────────
# Personal Access Token with `repo` scope — used to clone private contest repos
GITHUB_TOKEN: str = os.getenv("GITHUB_TOKEN", "")

# ── Paths ──────────────────────────────────────────────────────────────────────
CODEX_DIR = ROOT_DIR / ".codex"
REPOS_DIR = CODEX_DIR / "repos"

# Ensure dirs exist
CODEX_DIR.mkdir(parents=True, exist_ok=True)
REPOS_DIR.mkdir(parents=True, exist_ok=True)
