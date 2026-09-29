"""
Trufy API Client
----------------
Handles authentication and API calls against https://api.trufy.io.

Token caching: JWT is stored in .codex/trufy_token.json and reused for up to 23h.
"""
import sys
import json
import requests
from datetime import datetime
from pathlib import Path
from typing import Optional

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from shared.config import TRUFY_EMAIL, TRUFY_PASSWORD, TRUFY_API, CODEX_DIR

TOKEN_CACHE = CODEX_DIR / "trufy_token.json"
JWT_LIFETIME_HOURS = 23


# ── Token Caching ──────────────────────────────────────────────────────────────

def _load_cached_token() -> Optional[str]:
    """Return a cached JWT if it exists and is not stale."""
    if not TOKEN_CACHE.exists():
        return None
    try:
        data = json.loads(TOKEN_CACHE.read_text())
        obtained_at = datetime.fromisoformat(data["obtained_at"])
        age_hours = (datetime.utcnow() - obtained_at).total_seconds() / 3600
        if age_hours < JWT_LIFETIME_HOURS and data.get("token"):
            return data["token"]
    except Exception as e:
        print(f"[Trufy] Error loading cached token: {e}")
    return None


def _save_token(token: str):
    TOKEN_CACHE.write_text(json.dumps({
        "token": token,
        "obtained_at": datetime.utcnow().isoformat()
    }))


# ── Authentication ─────────────────────────────────────────────────────────────

def login() -> Optional[str]:
    """Login to Trufy and return JWT. Uses cached token if still valid."""
    cached = _load_cached_token()
    if cached:
        return cached

    if not TRUFY_EMAIL or not TRUFY_PASSWORD:
        print("[Trufy] ❌ Missing TRUFY_EMAIL or TRUFY_PASSWORD in .env")
        return None

    print("[Trufy] Logging in...")
    try:
        resp = requests.post(
            f"{TRUFY_API}/auth/login",
            json={"email": TRUFY_EMAIL, "password": TRUFY_PASSWORD},
            timeout=15
        )
        resp.raise_for_status()
        body = resp.json()
        jwt = body.get("jwt")
        if not jwt:
            print(f"[Trufy] ❌ Login succeeded but no JWT in response: {body}")
            return None
        _save_token(jwt)
        print(f"[Trufy] ✅ Logged in as {body.get('email')}")
        return jwt
    except Exception as e:
        print(f"[Trufy] ❌ Login failed: {e}")
        return None


# ── Source Code Upload ─────────────────────────────────────────────────────────

def upload_zip(zip_path: Path, jwt: str) -> Optional[str]:
    """
    Upload a zip file to Trufy.
    Returns the GCS URL to use as the project `url`.
    """
    print(f"[Trufy] Uploading {zip_path.name} ({zip_path.stat().st_size // 1024} KB)...")
    try:
        with open(zip_path, "rb") as f:
            resp = requests.post(
                f"{TRUFY_API}/common/upload/source-code",
                headers={"Authorization": f"Bearer {jwt}"},
                files={"file": (zip_path.name, f, "application/zip")},
                data={"framework": "any"},
                timeout=120
            )
        resp.raise_for_status()
        body = resp.json()
        url = body.get("url")
        if not url:
            print(f"[Trufy] ❌ Upload succeeded but no URL in response: {body}")
            return None
        print(f"[Trufy] ✅ Uploaded → {url}")
        return url
    except Exception as e:
        print(f"[Trufy] ❌ Upload failed: {e}")
        return None


# ── Project Management ─────────────────────────────────────────────────────────

def create_project(source_url: str, title: str, jwt: str) -> Optional[str]:
    """
    Create a Trufy project from an uploaded source URL.
    Returns the Trufy project_id.
    """
    print(f"[Trufy] Creating project: {title}")
    try:
        resp = requests.post(
            f"{TRUFY_API}/projects",
            headers={"Authorization": jwt},
            json={
                "source": "upload",
                "url": source_url,
                "branch": "",
                "title": title,
                "framework": "any"
            },
            timeout=30
        )
        resp.raise_for_status()
        body = resp.json()
        project_id = body.get("id")
        if not project_id:
            print(f"[Trufy] ❌ Project creation response missing id: {body}")
            return None
        print(f"[Trufy] ✅ Project created: {project_id}")
        return project_id
    except Exception as e:
        print(f"[Trufy] ❌ Project creation failed: {e}")
        return None


def trigger_scan(project_id: str, jwt: str) -> bool:
    """Trigger a Gemini-powered scan on the given project."""
    print(f"[Trufy] Triggering scan for project {project_id}...")
    try:
        resp = requests.post(
            f"{TRUFY_API}/projects/{project_id}/audit",
            headers={"Authorization": jwt},
            json={"tools": "gemini"},
            timeout=30
        )
        resp.raise_for_status()
        print(f"[Trufy] ✅ Scan triggered successfully.")
        return True
    except Exception as e:
        print(f"[Trufy] ❌ Scan trigger failed: {e}")
        return False


def _extract_findings(body) -> list:
    if isinstance(body, list):
        return body
    if not isinstance(body, dict):
        return []

    scan_task = body.get('scanTask', {})
    engine_tasks = scan_task.get('engineTasks', []) if isinstance(scan_task, dict) else []

    all_results = []
    for task in engine_tasks:
        res = task.get('results') or task.get('result') or []
        if isinstance(res, list):
            engine_id = task.get("id", "")
            engine_name = engine_id.split("_")[0] if "_" in engine_id else "unknown"
            for r in res:
                if isinstance(r, dict):
                    r["_engine"] = engine_name
            all_results.extend(res)

    if all_results:
        return all_results

    for key in ("results", "findings", "issues", "data", "items"):
        if key in body and isinstance(body[key], list):
            return body[key]

    return []

def _scan_is_finished(body) -> bool:
    if isinstance(body, list):
        return True
    if not isinstance(body, dict):
        return False

    statuses = []
    scan_task = body.get('scanTask', {})
    if isinstance(scan_task, dict):
        statuses.append(scan_task.get("status") or scan_task.get("state"))
        for task in scan_task.get("engineTasks", []) or []:
            statuses.append(task.get("status") or task.get("state"))

    normalized = [str(s).lower() for s in statuses if s]
    if not normalized:
        return False
    if any(s in ("running", "scanning", "pending", "queued", "processing", "in_progress") for s in normalized):
        return False
    return all(s in ("done", "completed", "complete", "success", "succeeded", "finished", "failed", "error") for s in normalized)

def get_scan_result_state(project_id: str, jwt: str) -> tuple[bool, list]:
    """
    Retrieve Trufy audit state.
    Returns (is_finished, findings). Empty findings with is_finished=False means poll again later.
    """
    print(f"[Trufy] Fetching scan results for project {project_id}...")
    try:
        resp = requests.get(
            f"{TRUFY_API}/projects/{project_id}/audit",
            headers={"Authorization": jwt},
            timeout=30
        )
        resp.raise_for_status()
        body = resp.json()
        findings = _extract_findings(body)
        return _scan_is_finished(body) or bool(findings), findings
    except Exception as e:
        print(f"[Trufy] ❌ Failed to fetch results: {e}")
        return False, []

def get_scan_results(project_id: str, jwt: str) -> list:
    """
    Retrieve AI audit findings for a project.
    Returns a list of issue dicts, or empty list on failure.
    """
    _, findings = get_scan_result_state(project_id, jwt)
    return findings
