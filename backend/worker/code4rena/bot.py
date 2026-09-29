"""
Code4rena Bot
-------------
Fetches contest data from code4rena.com using public API.

Authentication:
  - Public listing at /api/v1/audits.
  - Authenticated actions (join, etc.) use CODE4RENA_USERNAME/PASSWORD.
"""
import sys
import json
import argparse
import html
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional, List
import requests
import redis as redis_lib
import os

# Add worker root to path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from shared.config import CODE4RENA_USERNAME, CODE4RENA_PASSWORD, CODEX_DIR
from shared.db import (
    upsert_contest, get_bot_id, log_sync,
    get_contest, mark_joined, get_contest_by_platform_id,
    get_trufy_scan_for_contest, has_active_or_finished_trufy_scan,
    add_notification, upsert_issue
)

_redis_client = None
def _get_redis():
    global _redis_client
    if _redis_client is None:
        url = os.environ.get("REDIS_URL", "redis://redis:6379/0")
        _redis_client = redis_lib.Redis.from_url(url, decode_responses=True)
    return _redis_client

C4_BASE = "https://code4rena.com"
C4_API = f"{C4_BASE}/api/v1"
PLATFORM = "code4rena"
C4_COMPLETED_CAP = 5
C4_PAGE_SIZE = 20
C4_MAX_PAGES = 5

# ── Authentication ────────────────────────────────────────────────────────────

TOKEN_CACHE = CODEX_DIR / "code4rena_token.json"
JWT_LIFETIME_HOURS = 23

def _load_cached_token() -> Optional[str]:
    """Return a cached JWT if it exists and is not stale. None otherwise."""
    if not TOKEN_CACHE.exists():
        return None
    try:
        data = json.loads(TOKEN_CACHE.read_text())
        obtained_at = datetime.fromisoformat(data["obtained_at"])
        age_hours = (datetime.utcnow() - obtained_at).total_seconds() / 3600
        if age_hours < JWT_LIFETIME_HOURS and data.get("token"):
            return data["token"]
    except Exception as e:
        print(f"[Code4rena] Error loading cached token: {e}")
    return None

def _save_token(token: str):
    data = {
        "token": token,
        "obtained_at": datetime.utcnow().isoformat()
    }
    TOKEN_CACHE.write_text(json.dumps(data))

def login(session: Optional[requests.Session] = None) -> Optional[str]:
    """Authenticates and returns the JWT token string if successful."""
    if not CODE4RENA_USERNAME or not CODE4RENA_PASSWORD:
        print("[Code4rena] Missing credentials in .env (CODE4RENA_USERNAME, CODE4RENA_PASSWORD)")
        return None

    cached_token = _load_cached_token()
    if cached_token:
        if session:
            session.cookies.set("C4AUTH-LOGIN", cached_token, domain="code4rena.com")
        return cached_token

    print("[Code4rena] Logging in via API...")
    try:
        # If no session provided for login, use a temporary one
        auth_session = session if session else requests.Session()
        
        # 1. Fetch nonce
        resp = auth_session.get(f"{C4_API}/users/nonce", params={"handle": CODE4RENA_USERNAME})
        resp.raise_for_status()
        nonce = resp.json().get("nonce")
        if not nonce:
            print("[Code4rena] ❌ Failed to retrieve nonce.")
            return None

        # 2. Login
        login_payload = {
            "handle": CODE4RENA_USERNAME,
            "password": CODE4RENA_PASSWORD,
            "nonce": nonce
        }
        resp = auth_session.post(f"{C4_API}/users/session", params={"type": "password"}, json=login_payload)
        resp.raise_for_status()

        # 3. Extract and save token
        token = auth_session.cookies.get("C4AUTH-LOGIN", domain="code4rena.com")
        if token:
            _save_token(token)
            print("[Code4rena] ✅ Successfully logged in and cached token.")
            if session:
                session.cookies.set("C4AUTH-LOGIN", token, domain="code4rena.com")
            return token
        else:
            print("[Code4rena] ❌ Login request succeeded but no C4AUTH-LOGIN cookie found.")
            return None
            
    except Exception as e:
        print(f"[Code4rena] ❌ Login error: {e}")
        return None

def get_user_internal_id(session: requests.Session) -> Optional[str]:
    """Fetch the internal user ID for the configured handle."""
    try:
        resp = session.get(f"https://api.code4rena.com/api/get-user?id={CODE4RENA_USERNAME}", timeout=10)
        if resp.status_code == 200:
            return resp.json().get("id")
    except:
        pass
    return None

def _make_session(token: Optional[str] = None) -> requests.Session:
    session = requests.Session()
    session.headers.update({
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
        "Accept": "application/json",
    })
    return session

# ── Data Fetching ─────────────────────────────────────────────────────────────

def _parse_datetime(value) -> Optional[datetime]:
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if dt.tzinfo:
            return dt.astimezone(timezone.utc).replace(tzinfo=None)
        return dt
    except Exception:
        return None

def _map_status(raw_status: str, starts_at=None, ends_at=None, now: Optional[datetime] = None) -> str:
    # Map C4 statuses to our standard and let dates override stale/misleading API labels.
    now = now or datetime.utcnow()
    s = str(raw_status).lower()
    start_dt = _parse_datetime(starts_at)
    end_dt = _parse_datetime(ends_at)

    if s in ("completed", "complete", "finished", "ended", "closed", "cancelled", "canceled"):
        return "completed"
    if start_dt and start_dt > now:
        return "upcoming"
    if start_dt and start_dt <= now and (not end_dt or end_dt > now):
        return "active"
    if s in ("reporting", "triage", "review", "judging", "livejudging", "judging complete", "awarding", "sponsor review"):
        return "judging"
    if end_dt and end_dt <= now:
        return "completed"
    if s in ("active", "booking", "open"):
        return "active"
    if s in ("pre-contest", "pre-audit", "upcoming"):
        return "upcoming"
    return "completed"

def _sort_key(item: dict) -> str:
    return item.get("startTime") or item.get("startDate") or ""

def _normalize_contests(items: List[dict], now: Optional[datetime] = None) -> List[tuple]:
    now = now or datetime.utcnow()
    normalized = []
    for it in items:
        starts_at = it.get("startTime") or it.get("startDate")
        ends_at = it.get("endTime") or it.get("endDate")
        status = _map_status(it.get("status", "Completed"), starts_at, ends_at, now)
        normalized.append((it, status, starts_at, ends_at))
    normalized.sort(key=lambda row: _sort_key(row[0]), reverse=True)
    return normalized

def _select_sync_window(items: List[dict], now: Optional[datetime] = None) -> List[tuple]:
    selected = []
    completed_seen = 0

    for row in _normalize_contests(items, now):
        _, status, _, _ = row
        if status == "completed":
            if completed_seen >= C4_COMPLETED_CAP:
                continue
            completed_seen += 1
        selected.append(row)

    return selected

def fetch_all_contests(session: requests.Session) -> List[dict]:
    all_items = []
    page = 1
    while True:
        success = False
        for attempt in range(3):
            try:
                resp = session.get(
                    f"{C4_API}/audits",
                    params={"page": page, "perPage": C4_PAGE_SIZE, "sortBy": "startTime", "sortOrder": "desc"},
                    timeout=15
                )
                resp.raise_for_status()
                body = resp.json()
                success = True
                break
            except Exception as e:
                if attempt == 2:
                    print(f"[Code4rena] ❌ Fetch error at page {page} after 3 attempts: {e}")
                else:
                    print(f"[Code4rena] ⚠️  Attempt {attempt+1} failed at page {page}: {e}. Retrying...")
                    import time
                    time.sleep(2)
        
        if not success:
            break
            
        data = body.get("data", {})
        items = data.get("audits", []) if isinstance(data, dict) else (data if isinstance(data, list) else [])
        if not items: break
        all_items.extend(items)

        selected = _select_sync_window(all_items)
        selected_completed = sum(1 for _, status, _, _ in selected if status == "completed")
        if selected_completed >= C4_COMPLETED_CAP:
            break
        pagination = body.get("pagination", {})
        if pagination.get("nextPage") is None or page >= C4_MAX_PAGES: break
        page += 1
    return all_items

def check_enrollment(session: requests.Session, slug: str) -> bool:
    """Check if the authenticated user is enrolled in this audit."""
    try:
        resp = session.get(f"{C4_API}/audits/{slug}")
        if resp.status_code == 200:
            data = resp.json().get("data", {})
            return data.get("registered", False)
    except:
        pass
    return False

def _walk_dicts(value):
    if isinstance(value, dict):
        yield value
        for child in value.values():
            yield from _walk_dicts(child)
    elif isinstance(value, list):
        for child in value:
            yield from _walk_dicts(child)

def _walk_strings(value):
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for child in value.values():
            yield from _walk_strings(child)
    elif isinstance(value, list):
        for child in value:
            yield from _walk_strings(child)

def _extract_json_payloads(text: str) -> List[dict]:
    payloads = []
    match = re.search(
        r'<script[^>]+id=["\']__NEXT_DATA__["\'][^>]*>(.*?)</script>',
        text,
        flags=re.DOTALL | re.IGNORECASE,
    )
    if match:
        try:
            payloads.append(json.loads(html.unescape(match.group(1))))
        except Exception:
            pass

    for match in re.finditer(r"self\.__next_f\.push\((.*?)\)</script>", text, flags=re.DOTALL):
        try:
            payload = json.loads(match.group(1))
        except Exception:
            continue
        payloads.append(payload)
    return payloads

def _looks_like_submission(item: dict) -> bool:
    keys = {str(k).lower() for k in item.keys()}
    return bool(
        keys.intersection({"title", "finding", "issue", "risk", "severity"})
        and keys.intersection({"id", "_id", "uid", "submissionid", "submission_id"})
    )

def _extract_submission_dicts_from_text(value: str) -> List[dict]:
    submissions = []
    for match in re.finditer(r'\{[^{}]*"uid"[^{}]*"title"[^{}]*\}', value):
        try:
            submissions.append(json.loads(match.group(0)))
        except Exception:
            pass
    return submissions

def _extract_submissions_from_body(body) -> List[dict]:
    candidates = []
    for item in _walk_dicts(body):
        if _looks_like_submission(item):
            candidates.append(item)
        for value in item.values():
            if isinstance(value, str) and "submission" in value.lower():
                candidates.extend(_extract_submission_dicts_from_text(value))
    for value in _walk_strings(body):
        if "submission" in value.lower() and ("uid" in value or "title" in value):
            candidates.extend(_extract_submission_dicts_from_text(value))

    seen = set()
    unique = []
    for item in candidates:
        sid = item.get("id") or item.get("_id") or item.get("uid") or item.get("submissionId") or item.get("submission_id")
        key = str(sid or item.get("title") or item.get("finding") or "")
        if not key or key in seen:
            continue
        seen.add(key)
        unique.append(item)
    return unique

def _fetch_code4rena_user_submissions(session: requests.Session, slug: str, contest_id: Optional[str] = None, user_id: Optional[str] = None) -> List[dict]:
    submissions = []

    if contest_id:
        for page in range(1, 6):
            resp = session.get(
                f"{C4_API}/audits/{contest_id}/submissions",
                params={"perPage": 25, "page": page, "search": CODE4RENA_USERNAME},
                headers={"Accept": "application/json"},
                timeout=15,
            )
            print(f"[Code4rena] Submissions API page {page}: HTTP {resp.status_code}")
            if resp.status_code != 200:
                break

            try:
                page_items = _extract_submissions_from_body(resp.json())
            except Exception as e:
                print(f"[Code4rena] Could not parse submissions JSON: {e}")
                page_items = []

            if not page_items:
                break
            submissions.extend(page_items)

        if submissions:
            print(f"[Code4rena] Found {len(submissions)} submissions via JSON API for {slug}")
            return submissions

    for page in range(1, 6):
        resp = session.get(
            f"{C4_BASE}/audits/{slug}/submissions",
            params={"page": page, "search": CODE4RENA_USERNAME, "filter": "all"},
            headers={"Accept": "text/html,application/json"},
            timeout=15,
        )
        print(f"[Code4rena] Submissions page fallback {page}: HTTP {resp.status_code}")
        if resp.status_code != 200:
            break

        page_items = []
        content_type = resp.headers.get("content-type", "")
        if "application/json" in content_type:
            try:
                page_items = _extract_submissions_from_body(resp.json())
            except Exception:
                page_items = []
        else:
            for payload in _extract_json_payloads(resp.text):
                page_items.extend(_extract_submissions_from_body(payload))

        if not page_items:
            break
        submissions.extend(page_items)

    if submissions:
        print(f"[Code4rena] Found {len(submissions)} submissions via page fallback for {slug}")
        return submissions

    if not submissions:
        resp = session.get(f"{C4_API}/audits/{slug}/findings", timeout=10)
        print(f"[Code4rena] Findings fallback: HTTP {resp.status_code}")
        if resp.status_code == 200:
            body = resp.json()
            submissions = body.get("data", []) if isinstance(body, dict) else []

    if not submissions and contest_id and user_id:
        resp = session.get(f"{C4_API}/audits/{contest_id}/users/{user_id}/submissions", timeout=10)
        print(f"[Code4rena] User submissions fallback: HTTP {resp.status_code}")
        if resp.status_code == 200:
            body = resp.json()
            submissions = body.get("data", []) if isinstance(body, dict) else []

    return submissions

def _submission_text(item: dict, *keys: str, default: str = "") -> str:
    for key in keys:
        value = item.get(key)
        if value:
            if isinstance(value, dict):
                nested = value.get("title") or value.get("name") or value.get("label")
                if nested:
                    return str(nested)
            return str(value)
    return default

def _code4rena_verdict(item: dict) -> str:
    latest = item.get("latestEvaluations")
    if isinstance(latest, dict):
        for key in ("validity", "severity", "quality", "rank", "mitigationStatus"):
            value = latest.get(key)
            if value:
                return str(value)

    evaluations = item.get("evaluations")
    if isinstance(evaluations, list):
        for evaluation in reversed(evaluations):
            if isinstance(evaluation, dict) and evaluation.get("value"):
                return str(evaluation["value"])

    return _submission_text(item, "verdict", "status", "judgeStatus", "state", default="Waiting for response")

def _sync_submission_rows(contest_db_id: int, submissions: List[dict]) -> int:
    count = 0
    for sub in submissions:
        platform_id = (
            sub.get("id") or sub.get("_id") or sub.get("uid")
            or sub.get("submissionId") or sub.get("submission_id")
        )
        if not platform_id:
            continue

        title = _submission_text(sub, "title", "finding", "issue", default="Code4rena Submission")
        verdict = _code4rena_verdict(sub)
        comment = _submission_text(sub, "judgeComment", "comment", "reviewComment", "reason", "note")
        issue_id = upsert_issue({
            "contest_id": contest_db_id,
            "platform_issue_id": f"c4-{platform_id}",
            "title": title,
            "description": _submission_text(sub, "description", "body", "details"),
            "severity": _submission_text(sub, "severity", "risk", default="Unknown"),
            "status": "submitted",
            "judge_verdict": verdict,
            "judge_status": verdict,
        })
        if comment:
            add_notification(contest_db_id, f"Judge response on {title}: {comment}", issue_id=issue_id)
        count += 1
    return count

def sync_submissions(session: requests.Session, contest_db_id: int, slug: str, contest_id: Optional[str] = None, user_id: Optional[str] = None):
    """Fetch existing findings and judge comments for a contest."""
    try:
        submissions = _fetch_code4rena_user_submissions(session, slug, contest_id, user_id)
        synced = _sync_submission_rows(contest_db_id, submissions)
        if synced:
            print(f"[Code4rena] Synced {synced} submissions for {slug}")
    except Exception as e:
        print(f"[Code4rena] ⚠️  Submission sync failed for {slug}: {e}")

# ── Core Commands ─────────────────────────────────────────────────────────────

def sync(session: requests.Session):
    print(f"[Code4rena] Starting sync...")

    bot_id = get_bot_id(PLATFORM)
    if not bot_id:
        msg = "No active Code4rena bot found in DB"
        print(f"[Code4rena] ❌ {msg}")
        log_sync(PLATFORM, 0, 0, "error", msg)
        return

    # Fetch user internal ID once for the sync
    user_id = get_user_internal_id(session)

    items = fetch_all_contests(session)
    normalized = _select_sync_window(items)
    print(f"[Code4rena] Fetched {len(items)} raw contests; selected {len(normalized)} for sync")
    
    new_count = 0
    now = datetime.utcnow()

    for it, status, starts_at, ends_at in normalized:
        slug = it.get("slug")
        if not slug:
            continue
        platform_contest_id = f"code4rena-{slug}"

        start_dt = _parse_datetime(starts_at)
        end_dt = _parse_datetime(ends_at)
        has_started = not start_dt or start_dt <= now
        is_ended = bool(end_dt and end_dt <= now)

        # URLs
        platform_url = f"{C4_BASE}/audits/{slug}"
        repo_url = it.get("repo", "")
        
        params = {
            "bot_id": bot_id,
            "platform_contest_id": platform_contest_id,
            "title": it.get("title", slug),
            "code_url": json.dumps([repo_url] if repo_url else []),
            "platform_url": platform_url,
            "description": "",
        }
        
        # We MUST fetch the full README from GitHub because the LLM needs the full scope, 
        # out-of-scope, invariants, and architecture context to accurately filter findings.
        if repo_url:
            try:
                repo_path = repo_url.replace("https://github.com/", "").strip("/")
                import requests, base64
                resp = requests.get(f"https://api.github.com/repos/{repo_path}/readme", timeout=10)
                if resp.status_code == 200:
                    encoded_content = resp.json().get("content", "")
                    params["description"] = base64.b64decode(encoded_content).decode('utf-8')
            except Exception as e:
                print(f"[C4 Bot] Failed to fetch README for {slug}: {e}")
                
        # Fallback if README fetch fails
        if not params["description"]:
            params["description"] = it.get("details") or it.get("description", "")
                
        params.update({
            "prize_pool": float(it.get("awardPool", 0)) if it.get("awardPool") else 0,
            "token": it.get("currency", "USDC"),
            "logo_url": it.get("org", {}).get("image", "") if isinstance(it.get("org"), dict) else "",
            "synced_from": PLATFORM,
            "starts_at": starts_at,
            "ends_at": ends_at,
            "is_finalized": status == "completed",
        })

        # Check Enrollment (Fix for Monetrix)
        has_joined = check_enrollment(session, slug)
        params["has_joined"] = has_joined

        db_contest = get_contest_by_platform_id(params["platform_contest_id"])
        
        # Override status to 'auditing' if a Trufy scan is currently in progress
        local_status = status
        if db_contest:
            scan = get_trufy_scan_for_contest(db_contest["id"])
            if scan and scan.get("status") in ("pending", "scanning"):
                local_status = "auditing"
        
        params["status"] = local_status
        is_new = upsert_contest(params)
        
        if not db_contest:
            db_contest = get_contest_by_platform_id(params["platform_contest_id"])

        if db_contest:
            if (
                not has_joined
                and status == "active"
                and has_started
                and not is_ended
                and not db_contest.get("skip_autojoin")
            ):
                try:
                    _get_redis().lpush("jobs", json.dumps({
                        "action": "join_contest",
                        "contest_id": db_contest["id"],
                        "platform": PLATFORM
                    }))
                    print(f"  ⚡ Join queued for '{params['title']}'")
                except Exception as re:
                    print(f"  ⚠️  Could not enqueue join: {re}")

            # Sync submissions if joined
            if has_joined:
                sync_submissions(session, db_contest["id"], slug, it.get("id"), user_id)

            # Auto-audit logic
            if has_joined and repo_url:
                if not has_started:
                    print(f"  ⏳ Joined but not started yet; scan deferred for '{params['title']}'")
                elif is_ended:
                    print(f"  ⏭️  Contest ended; Trufy scan skipped for '{params['title']}'")
                elif not has_active_or_finished_trufy_scan(db_contest["id"]):
                    try:
                        _get_redis().lpush("jobs", json.dumps({
                            "action": "trufy_scan",
                            "contest_id": db_contest["id"]
                        }))
                        print(f"  🤖 Trufy scan queued for '{params['title']}'")
                    except Exception as re:
                        print(f"  ⚠️  Could not enqueue Trufy scan: {re}")

        if is_new:
            new_count += 1
            print(f"  ✨ New: {params['title']} [{status}]")
        else:
            print(f"  ↻  Updated: {params['title']} [{status}]")

    log_sync(PLATFORM, len(normalized), new_count)
    print(f"[Code4rena] ✅ Sync done — {len(normalized)} selected, {new_count} new")

def join(session: requests.Session, contest_db_id: int) -> bool:
    """Join a Code4rena contest using the RSVP endpoint."""
    print(f"[Code4rena] Join requested for DB ID {contest_db_id}...")
    
    contest = get_contest(contest_db_id)
    if not contest:
        print(f"[Code4rena] ❌ Contest {contest_db_id} not found in DB.")
        return False

    # DB stores platform_contest_id as "code4rena-{slug}"
    db_id_str = contest.get("platform_contest_id", "")
    slug = db_id_str.replace("code4rena-", "")
    
    if not slug:
        print(f"[Code4rena] ❌ Could not extract slug from DB.")
        return False

    # Authenticate
    if not login(session):
        print("[Code4rena] ❌ Authentication failed. Cannot join contest.")
        return False

    # Fetch contest details from API to get the specific uid required for RSVP
    print(f"[Code4rena] Looking up internal UID for contest slug '{slug}'...")
    try:
        resp = session.get(f"{C4_API}/audits", params={"perPage": 50})
        resp.raise_for_status()
        body = resp.json()
        data = body.get("data", {})
        items = data.get("audits", []) if isinstance(data, dict) else (data if isinstance(data, list) else [])
        
        target_uid = None
        for item in items:
            if item.get("slug") == slug:
                target_uid = item.get("uid")
                break
                
        if not target_uid:
            print(f"[Code4rena] ❌ Could not find active contest with slug '{slug}' via API.")
            return False

        # RSVP to join
        print(f"[Code4rena] Sending RSVP for UID {target_uid}...")
        resp = session.put(f"{C4_API}/audits/{target_uid}/rsvp")
        resp.raise_for_status()
        
        mark_joined(contest_db_id)
        print(f"[Code4rena] ✅ Successfully joined contest: {slug}")
        return True

    except Exception as e:
        print(f"[Code4rena] ❌ Failed to join contest: {e}")
        return False

def check_responses(session: requests.Session, contest_id: Optional[int] = None, force: bool = False):
    print(f"[Code4rena] Checking responses for contest ID: {contest_id if contest_id else 'ALL'} (force={force})")

    if not contest_id:
        return

    contest = get_contest(contest_id)
    if not contest:
        print(f"[Code4rena] ❌ Contest {contest_id} not found in DB")
        return
    if contest.get("is_finalized") and not force:
        print(f"[Code4rena] 💤 Contest {contest_id} is finalized. Skipping response check.")
        return

    slug = str(contest.get("platform_contest_id") or "").replace("code4rena-", "")
    if not slug:
        print(f"[Code4rena] ❌ Could not extract slug for contest {contest_id}")
        return

    if not login(session):
        print("[Code4rena] ❌ Authentication failed. Cannot check submissions.")
        return

    user_id = get_user_internal_id(session)
    try:
        resp = session.get(f"{C4_API}/audits/{slug}", timeout=10)
        audit_id = None
        if resp.status_code == 200:
            body = resp.json()
            data = body.get("data", body) if isinstance(body, dict) else {}
            audit_id = data.get("uid") or data.get("id")
            print(f"[Code4rena] Audit uid for {slug}: {audit_id or 'not found'}")
        else:
            print(f"[Code4rena] Audit detail fetch failed for {slug}: HTTP {resp.status_code}")
        sync_submissions(session, contest_id, slug, audit_id, user_id)
    except Exception as e:
        print(f"[Code4rena] ⚠️  Response check failed for {slug}: {e}")

def status(token: Optional[str], session: requests.Session) -> dict:
    connected = False
    authenticated = False
    
    try:
        r = session.get(f"{C4_API}/audits", params={"perPage": 1}, timeout=5)
        connected = r.status_code == 200
    except:
        pass

    if connected:
        authenticated = login(session) is not None

    return {
        "platform": "code4rena",
        "connected": connected,
        "authenticated": authenticated,
        "auth_method": "credentials",
        "note": "Using active session token." if authenticated else "Not authenticated. Missing or invalid credentials."
    }

def _submission_body(issue: dict) -> str:
    report = (issue.get("submission_report") or "").strip()
    if report:
        without_poc = re.split(r"\n#{1,3}\s*Proof of Concept\b", report, maxsplit=1, flags=re.I)[0].strip()
        return without_poc or report
    return (issue.get("description") or "").strip()

def _first_repo_url(code_url) -> str:
    if not code_url:
        return ""
    if isinstance(code_url, str):
        try:
            parsed = json.loads(code_url)
            if isinstance(parsed, list):
                return str(parsed[0]) if parsed else ""
            if isinstance(parsed, str):
                return parsed
        except Exception:
            return code_url
    if isinstance(code_url, list):
        return str(code_url[0]) if code_url else ""
    return ""

def _submission_lines_of_code(issue: dict, contest: Optional[dict] = None) -> str:
    text = "\n".join(
        str(issue.get(key) or "")
        for key in ("submission_report", "description")
    )
    matches = []
    pattern = re.compile(
        r"([A-Za-z0-9_./-]+\.(?:sol|rs|vy|move|cairo|ts|tsx|js|jsx))"
        r"(?:(?::L?|\s+L)(\d+)(?:-L?(\d+))?)?"
    )
    for match in pattern.finditer(text):
        path, start, end = match.groups()
        if not path.startswith(("contracts/", "src/", "apps/", "packages/", "protocol/")):
            continue
        loc = f"{path}:{start}" if start else path
        if end and end != start:
            loc = f"{loc}-{end}"
        if loc not in matches:
            matches.append(loc)

    repo_url = _first_repo_url((contest or {}).get("code_url"))
    if repo_url:
        repo_url = repo_url.rstrip("/")
        urls = []
        for loc in matches[:10]:
            path, _, line_part = loc.partition(":")
            anchor = ""
            if line_part:
                start, _, end = line_part.partition("-")
                anchor = f"#L{start}"
                if end and end != start:
                    anchor += f"-L{end}"
            urls.append(f"{repo_url}/blob/main/{path}{anchor}")
        if urls:
            return "\n".join(urls)

    return "\n".join(matches[:10])

def submit_finding_to_platform(issue: dict, session: requests.Session = None) -> str:
    """
    Submits a finding to Code4rena.
    Returns the platform issue ID.
    """
    if session is None:
        session = _make_session()
        login(session)
    
    print(f"[C4Bot] 🚀 Submitting issue '{issue.get('title')}' to Code4rena...")
    
    contest = get_contest(issue["contest_id"])
    if not contest:
        print(f"[C4Bot] ❌ Contest {issue['contest_id']} not found in DB.")
        return None
        
    db_id_str = contest.get("platform_contest_id", "")
    slug = db_id_str.replace("code4rena-", "")
    if not slug:
        print(f"[C4Bot] ❌ Could not extract slug from DB.")
        return None

    # Fetch audit ID
    audit_uid = None
    try:
        r = session.get(f"{C4_API}/audits/{slug}")
        r.raise_for_status()
        audit_uid = r.json().get("uid")
    except Exception as e:
        print(f"[C4Bot] ❌ Failed to fetch audit {slug}: {e}")
        return None

    if not audit_uid:
        print(f"[C4Bot] ❌ No uid found for audit {slug}.")
        return None
        
    # Map severity to lowercase string expected by C4 API
    severity_mapping = {
        "High": "high",
        "Medium": "medium",
        "Low": "qa",
        "Informational": "qa"
    }
    severity_val = severity_mapping.get(issue.get("severity"), "medium")
    body = _submission_body(issue)
    poc = (issue.get("poc_code") or "").strip()
    lines_of_code = _submission_lines_of_code(issue, contest)
    
    # Construct Payload
    payload = {
        "teamUid": None,
        "title": issue.get("title", "Found an issue"),
        "body": body,
        "proofOfConcept": poc,
        "linesOfCode": lines_of_code,
        "severity": severity_val
    }
    
    try:
        post_url = f"{C4_API}/audits/{audit_uid}/submissions"
        print(f"[C4Bot] POST {post_url}")
        resp = session.post(post_url, json=payload)
        resp.raise_for_status()
        
        # Extract submission ID
        res_data = resp.json()
        sub_id = res_data.get("id") or res_data.get("uid")
        
        if not sub_id:
            import uuid
            sub_id = f"c4-unknown-{uuid.uuid4().hex[:8]}"
            
        print(f"[C4Bot] ✅ Submission successful. Platform ID: {sub_id}")
        return sub_id
        
    except Exception as e:
        print(f"[C4Bot] ❌ Failed to submit finding to Code4rena: {e}")
        try:
            print(f"[C4Bot] Response: {resp.text}")
        except:
            pass
        return None

# ── Entry Point ───────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Code4rena Bot")
    parser.add_argument("--sync", action="store_true", help="Sync contests")
    parser.add_argument("--status", action="store_true", help="Print status")
    args = parser.parse_args()

    session = _make_session()

    if args.status:
        print(json.dumps(status(None, session), indent=2))
    elif args.sync:
        sync(session)
    else:
        parser.print_help()

if __name__ == "__main__":
    main()
