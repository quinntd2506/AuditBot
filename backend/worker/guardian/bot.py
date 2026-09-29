from typing import Optional, List
"""
Guardian Defender Bot
---------------------
Fetches contest data from api.guardianaudits.com and clones contest repos.

Authentication:
  - Logs in with GUARDIAN_EMAIL + GUARDIAN_PASSWORD from .env.
  - JWT is cached to .codex/guardian_token.json and reused for ~23 hours.
  - Re-logs in automatically when the cached token expires.

Usage:
  python3 bots/guardian/bot.py --sync            # Fetch and store contests
  python3 bots/guardian/bot.py --status          # Print auth + connection info
"""
import sys
import json
import argparse
from datetime import datetime
from pathlib import Path

import requests
import redis
import os

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from shared.config import GUARDIAN_EMAIL, GUARDIAN_PASSWORD, CODEX_DIR, REPOS_DIR
from shared.db import (
    upsert_contest, get_bot_id, log_sync, 
    add_notification, get_contest, upsert_issue,
    get_contest_by_platform_id, mark_joined,
    get_trufy_scan_for_contest, has_active_or_finished_trufy_scan
)

_redis_client = None
def _get_redis():
    global _redis_client
    if _redis_client is None:
        url = os.environ.get("REDIS_URL", "redis://redis:6379/0")
        _redis_client = redis.Redis.from_url(url, decode_responses=True)
    return _redis_client

GUARDIAN_API = "https://api.guardianaudits.com"
PLATFORM = "guardian"
TOKEN_CACHE = CODEX_DIR / "guardian_token.json"

# JWT is assumed valid for 23 hours (Guardian doesn't expose expiry in the response)
JWT_LIFETIME_HOURS = 23


# ── Authentication ─────────────────────────────────────────────────────────────

def _load_cached_token() -> Optional[str]:
    """Return a cached JWT if it exists and is not stale. None otherwise."""
    if not TOKEN_CACHE.exists():
        return None
    try:
        data = json.loads(TOKEN_CACHE.read_text())
        obtained_at = datetime.fromisoformat(data["obtained_at"])
        age_hours = (datetime.utcnow() - obtained_at).total_seconds() / 3600
        if age_hours < JWT_LIFETIME_HOURS and data.get("token"):
            print(f"[Guardian] 🔑 Using cached JWT (age: {age_hours:.1f}h / {JWT_LIFETIME_HOURS}h)")
            return data["token"]
        print(f"[Guardian] 🔄 Cached JWT expired ({age_hours:.1f}h old) — re-logging in")
    except Exception as e:
        print(f"[Guardian] ⚠️  Could not read token cache: {e}")
    return None


def _save_token(token: str):
    """Write the JWT to disk so subsequent invocations can reuse it."""
    TOKEN_CACHE.parent.mkdir(parents=True, exist_ok=True)
    TOKEN_CACHE.write_text(json.dumps({
        "token": token,
        "obtained_at": datetime.utcnow().isoformat(),
    }))
    print(f"[Guardian] 💾 JWT cached to {TOKEN_CACHE.relative_to(TOKEN_CACHE.parent.parent.parent)}")


def login() -> Optional[str]:
    """
    Obtain a valid JWT.
    1. Try the on-disk cache first (avoids a login request if still fresh).
    2. If stale or missing, POST to /api/auth/login with .env credentials.
    """
    cached = _load_cached_token()
    if cached:
        return cached

    if not GUARDIAN_EMAIL or not GUARDIAN_PASSWORD:
        print("[Guardian] ❌ No credentials in .env.")
        print("           Set GUARDIAN_EMAIL and GUARDIAN_PASSWORD in your .env file.")
        return None

    print(f"[Guardian] 🔐 Logging in as {GUARDIAN_EMAIL}...")
    try:
        resp = requests.post(
            f"{GUARDIAN_API}/api/auth/login",
            json={"email": GUARDIAN_EMAIL, "password": GUARDIAN_PASSWORD},
            headers={"Accept": "application/json", "Content-Type": "application/json"},
            timeout=12,
        )
        resp.raise_for_status()
        body = resp.json()

        if body.get("status") == "success":
            # Token is a top-level field in the response
            token = body.get("token") or body.get("data", {}).get("token")
            if not token:
                print(f"[Guardian] ❌ No token in response: {list(body.keys())}")
                return None
            username = body.get("data", {}).get("user", {}).get("username", GUARDIAN_EMAIL)
            print(f"[Guardian] ✅ Logged in as {username}")
            _save_token(token)
            return token

        msg = body.get("message", "Unknown error")
        print(f"[Guardian] ❌ Login rejected: {msg}")
        return None

    except requests.HTTPError as e:
        body_text = e.response.text[:300] if e.response else "no body"
        print(f"[Guardian] ❌ HTTP {e.response.status_code}: {body_text}")
        return None
    except Exception as e:
        print(f"[Guardian] ❌ Login error: {e}")
        return None


def _make_session(token: str) -> requests.Session:
    session = requests.Session()
    session.headers.update({
        "Accept": "application/json",
        "Content-Type": "application/json",
        "Authorization": f"Bearer {token}",
    })
    return session


# ── Data Fetching ─────────────────────────────────────────────────────────────

def _compute_prize_pool(raw: dict) -> float:
    if raw.get("prizePool"):
        return float(raw["prizePool"])
    pools = raw.get("rewardPools") or {}
    return float(sum(
        (pools.get(tier) or {}).get("maxPool", 0)
        for tier in ("critical", "high", "medium")
    ))


def _map_status(raw: dict) -> str:
    # Use API flags directly as requested
    is_active = raw.get("isActive", False)
    
    if is_active:
        return "active"
    
    # If not active, it's likely judging or completed
    return "judging"

def _parse_datetime(value) -> Optional[datetime]:
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).replace(tzinfo=None)
    except Exception:
        return None


def _extract_response_items(body) -> List[dict]:
    if isinstance(body, list):
        return body
    if not isinstance(body, dict):
        return []

    data = body.get("data")
    candidates = [data, body.get("contests"), body.get("items")]
    if isinstance(data, dict):
        candidates.extend([data.get("contests"), data.get("items")])

    for candidate in candidates:
        if isinstance(candidate, list):
            return [item for item in candidate if isinstance(item, dict)]
    return []


def _extract_response_object(body) -> dict:
    if not isinstance(body, dict):
        return {}
    data = body.get("data")
    if isinstance(data, dict):
        for key in ("contest", "item"):
            nested = data.get(key)
            if isinstance(nested, dict):
                return nested
        return data
    for key in ("contest", "item"):
        nested = body.get(key)
        if isinstance(nested, dict):
            return nested
    return body


def _first_present(raw: dict, *keys):
    for key in keys:
        value = raw.get(key)
        if value not in (None, "", [], {}):
            return value
    return None


def _contest_id(raw: dict):
    return _first_present(raw, "id", "_id", "contestId")


_DATE_PLACEHOLDERS = {"tba", "n/a", "tbd"}


def _is_placeholder_date(value) -> bool:
    """Return True if the value looks like a placeholder rather than a real date."""
    if not value or not isinstance(value, str):
        return False
    return value.strip().lower() in _DATE_PLACEHOLDERS


def _starts_at(raw: dict):
    value = _first_present(raw, "starts_at", "startDate", "start_date", "startsAt", "startTime", "start_time")
    return None if _is_placeholder_date(value) else value


def _ends_at(raw: dict):
    value = _first_present(raw, "ends_at", "endDate", "end_date", "endsAt", "endTime", "end_time")
    return None if _is_placeholder_date(value) else value


def _append_repo_url(repo_urls: List[str], value):
    if not value:
        return
    if isinstance(value, str):
        url = value.strip()
        if url:
            repo_urls.append(url)
        return
    if isinstance(value, list):
        for item in value:
            _append_repo_url(repo_urls, item)
        return
    if isinstance(value, dict):
        for key in ("url", "repo_url", "githubRepoUrl", "githubUrl", "cloneUrl", "html_url", "href", "link"):
            if value.get(key):
                _append_repo_url(repo_urls, value.get(key))
                return
        for key in ("repository", "repo"):
            if value.get(key):
                _append_repo_url(repo_urls, value.get(key))
                return


def _repo_urls(raw: dict) -> List[str]:
    repo_urls: List[str] = []
    for field in (
        "repo_url",
        "githubRepoUrl",
        "githubRepoUrls",
        "repositories",
        "repository",
        "repos",
        "repo",
        "githubLinks",
        "code",
    ):
        _append_repo_url(repo_urls, raw.get(field))
    return list(dict.fromkeys(repo_urls))


def _merge_contest_summary(summary: dict, detail: dict) -> dict:
    merged = dict(summary)
    for key, value in detail.items():
        if value in (None, "", [], {}):
            continue
        if merged.get(key) in (None, "", [], {}):
            merged[key] = value
    return merged


def _fetch_contest_detail(session: requests.Session, platform_id) -> dict:
    try:
        resp = session.get(f"{GUARDIAN_API}/api/contests/{platform_id}", timeout=12)
        if resp.status_code != 200:
            return {}
        return _extract_response_object(resp.json())
    except Exception as e:
        print(f"[Guardian] ⚠️  Could not fetch contest detail for {platform_id}: {e}")
        return {}


def fetch_all_contests(session: requests.Session) -> List[dict]:
    contests = []
    page = 1
    while True:
        resp = session.get(
            f"{GUARDIAN_API}/api/contests",
            params={"page": page, "limit": 20},
            timeout=12,
        )
        resp.raise_for_status()
        body = resp.json()

        if body.get("status") != "success":
            print(f"[Guardian] ⚠️  Unexpected response: {body.get('message')}")
            break

        batch = body["data"]["contests"]
        contests.extend(batch)
        pagination = body["data"]["pagination"]
        print(f"  Page {page}/{pagination['pages']} — {len(batch)} contests")

        if page >= pagination["pages"]:
            break
        page += 1

    return contests


# ── Core Commands ─────────────────────────────────────────────────────────────

def join(session: requests.Session, contest_db_id: int) -> bool:
    """Attempt to join a contest on the Guardian platform."""
    contest = get_contest(contest_db_id)
    if not contest:
        print(f"[Guardian] ❌ Contest {contest_db_id} not found in DB")
        return False

    platform_cid = contest["platform_contest_id"].replace("guardian-", "")
    title = contest.get("title", f"ID:{contest_db_id}")

    print(f"[Guardian] 🤝 Attempting to join: {title} (platform_id={platform_cid})...")
    try:
        resp = session.post(
            f"{GUARDIAN_API}/api/contests/{platform_cid}/join",
            json={},
            timeout=12,
        )
        if resp.status_code in (200, 201):
            print(f"[Guardian] ✅ Successfully joined: {title}")
            return True
        return False
    except Exception as e:
        print(f"[Guardian] ❌ Join error: {e}")
        return False

def sync_submissions(session: requests.Session, contest_db_id: int, platform_id: int):
    """Fetch user's findings for a specific Guardian contest."""
    try:
        resp = session.get(f"{GUARDIAN_API}/api/contests/{platform_id}/submissions", timeout=10)
        if resp.status_code != 200:
            return

        body = resp.json()
        if isinstance(body, list):
            findings = body
        elif isinstance(body, dict):
            data = body.get("data", body)
            findings = data if isinstance(data, list) else data.get("issues", []) or data.get("submissions", [])
        else:
            findings = []

        for f in findings:
            platform_id_f = f.get("id") or f.get("_id")
            if not platform_id_f:
                continue
            verdict = f.get("status", "Waiting for response")
            note = f.get("invalidationReason") or f.get("reviewNote") or f.get("judgeComment")
            issue_id = upsert_issue({
                "contest_id": contest_db_id,
                "platform_issue_id": f"guardian-{platform_id_f}",
                "title": f.get("title", "Guardian Finding"),
                "description": f.get("description", ""),
                "severity": f.get("severity") or "Unknown",
                "status": "submitted",
                "judge_verdict": verdict,
                "judge_status": verdict,
            })
            if note:
                add_notification(contest_db_id, note, issue_id=issue_id)
    except Exception as e:
        print(f"[Guardian] ⚠️  Submission sync failed for contest {platform_id}: {e}")

def sync(session: requests.Session):
    """
    Main sync logic for Guardian Audits.
    Fetches the contest list, filters for NEW ones, and upserts them.
    """
    print("[Guardian] 🔄 Starting sync...")

    bot_id = get_bot_id("guardian")
    if not bot_id:
        print("[Guardian] ❌ No active bot found in DB. Skipping.")
        return

    for attempt in range(3):
        try:
            resp = session.get(f"{GUARDIAN_API}/api/contests", timeout=30)
            resp.raise_for_status()
            body = resp.json()
            items = _extract_response_items(body)
            break # Success
        except Exception as e:
            if attempt == 2:
                print(f"[Guardian] ❌ Failed to fetch contest list after 3 attempts: {e}")
                return
            print(f"[Guardian] ⚠️  Attempt {attempt+1} failed: {e}. Retrying...")
            import time
            time.sleep(2)

    # Sort items by date descending
    if items:
        items.sort(key=lambda x: _starts_at(x) or "", reverse=True)

    new_count = 0
    now = datetime.utcnow()
    normalized = []

    for it in items:
        platform_id = _contest_id(it)
        if platform_id and (not _starts_at(it) or not _ends_at(it) or not _repo_urls(it)):
            detail = _fetch_contest_detail(session, platform_id)
            if detail:
                it = _merge_contest_summary(it, detail)

        status_raw = str(it.get("status") or "completed").lower()
        contest_status = "completed"
        if status_raw in ("active", "running", "live"):
            contest_status = "active"
        elif status_raw in ("upcoming", "created"):
            contest_status = "upcoming"
        elif status_raw in ("judging", "reporting", "review"):
            contest_status = "auditing"

        starts_at = _starts_at(it)
        ends_at = _ends_at(it)
        start_dt = _parse_datetime(starts_at)
        end_dt = _parse_datetime(ends_at)
        if contest_status != "auditing":
            if end_dt and end_dt <= now:
                contest_status = "completed"
            elif start_dt and start_dt > now:
                contest_status = "upcoming"
            elif start_dt and start_dt <= now and (not end_dt or end_dt > now):
                contest_status = "active"
        normalized.append((it, contest_status, starts_at, ends_at, start_dt, end_dt))

    has_active = any(status == "active" for _, status, *_ in normalized)
    completed_seen = 0

    for it, contest_status, starts_at, ends_at, start_dt, end_dt in normalized:
        platform_id = _contest_id(it)
        if not platform_id: continue

        # 1. History Capping
        if contest_status == "completed":
            if has_active:
                continue
            completed_seen += 1
            if completed_seen > 5:
                continue

        # 2. Strict End Date Check
        has_started = not start_dt or start_dt <= now
        is_ended = bool(end_dt and end_dt <= now)

        repo_urls = _repo_urls(it)
        
        params = {
            "bot_id": bot_id,
            "platform_contest_id": f"guardian-{platform_id}",
            "title": it.get("title", ""),
            "code_url": json.dumps(repo_urls),
            "platform_url": f"https://defender.guardianaudits.com/contests/{platform_id}",
            "status": contest_status,
            "description": it.get("description", ""),
            "prize_pool": it.get("prize_pool", 0),
            "token": it.get("token", "USDC"),
            "logo_url": it.get("logo_url", ""),
            "synced_from": PLATFORM,
            "starts_at": starts_at,
            "ends_at": ends_at,
            "is_finalized": contest_status == "completed",
        }

        # Override status to 'auditing' if a Trufy scan is currently in progress
        local_status = contest_status
        db_contest = get_contest_by_platform_id(params["platform_contest_id"])
        if db_contest:
            scan = get_trufy_scan_for_contest(db_contest["id"])
            if scan and scan.get("status") in ("pending", "scanning"):
                local_status = "auditing"
        
        params["status"] = local_status

        # 3. Enrollment Detection
        has_joined = it.get("is_enrolled", False) or it.get("joined", False)
        params["has_joined"] = has_joined

        is_new = upsert_contest(params)
        
        # Fetch the DB row so we have the id and has_joined flag
        db_contest = get_contest_by_platform_id(params["platform_contest_id"])

        if db_contest:
            # Sync submissions
            if has_joined:
                sync_submissions(session, db_contest["id"], platform_id)

            # 4. Auto-Audit Logic
            if has_joined and repo_urls:
                if not has_started:
                    print(f"  ⏳ Joined but not started yet; scan deferred for '{it['title']}'")
                elif is_ended:
                    print(f"  ⏭️  Contest ended; Trufy scan skipped for '{it['title']}'")
                elif not has_active_or_finished_trufy_scan(db_contest["id"]):
                    try:
                        _get_redis().lpush("jobs", json.dumps({
                            "action": "trufy_scan",
                            "contest_id": db_contest["id"]
                        }))
                        print(f"  🤖 Trufy scan queued for '{it['title']}'")
                    except Exception as re:
                        print(f"  ⚠️  Could not enqueue Trufy scan: {re}")

        if is_new:
            new_count += 1
            print(f"  ✨ New: {it.get('title')} [{contest_status}]")
        else:
            print(f"  ↻  Updated: {it.get('title')} [{contest_status}]")

    log_sync(PLATFORM, len(items), new_count)
    print(f"[Guardian] ✅ Sync done — {len(items)} fetched, {new_count} new")


def check_responses(session: requests.Session, contest_db_id: int, force: bool = False):
    """Fetch submissions and record new internal notifications for judge responses."""
    print(f"[Guardian] Checking responses for contest {contest_db_id} (force={force})...")
    
    contest = get_contest(contest_db_id)
    if not contest:
        print(f"[Guardian] ❌ Contest {contest_db_id} not found in DB")
        return
        
    if contest.get("is_finalized") and not force:
        print(f"[Guardian] 💤 Contest {contest_db_id} is finalized. Skipping response check.")
        return

    # extract the platform-specific _id
    platform_cid = contest["platform_contest_id"].replace("guardian-", "")
    
    try:
        # Guardian API endpoint for user submissions
        resp = session.get(f"{GUARDIAN_API}/api/issues")
        if not resp.ok:
            print(f"[Guardian] ❌ Failed to fetch submissions: {resp.status_code}")
            return
            
        body = resp.json()
        data = body.get('data', {})
        submissions = []
        
        if isinstance(data, list):
            submissions = data
        elif isinstance(data, dict):
            submissions = data.get('issues', []) or data.get('submissions', [])
            
        if not submissions:
            print(f"[Guardian] Debug: No submissions found in body: {body}")
            
        relevant = []
        for s in submissions:
            # contestId can be a string or a full object
            val = s.get('contestId') or s.get('contest')
            s_cid = ""
            if isinstance(val, dict):
                s_cid = str(val.get('_id', val.get('id', '')))
            else:
                s_cid = str(val or '')
                
            if s_cid.strip() == platform_cid.strip():
                relevant.append(s)
                
        print(f"[Guardian] Found {len(relevant)} submissions for this contest")
        
        for sub in relevant:
            status = sub.get('status', 'pending').lower()
            note = sub.get('invalidationReason') or sub.get('reviewNote')
            title = sub.get('title', 'Unknown Issue')
            p_id = sub.get('_id')
            
            # Map platform status to our local status
            local_status = 'pending'
            if status == 'valid' or status == 'accepted':
                local_status = 'valid'
            elif status == 'invalid' or status == 'rejected':
                local_status = 'invalid'
                
            # Sync to issues table
            issue_id = upsert_issue({
                "contest_id": contest_db_id,
                "platform_issue_id": f"guardian-{p_id}",
                "title": title,
                "description": sub.get('description', ''),
                "severity": sub.get('severity') or 'Unknown',
                "status": local_status,
                "judge_verdict": sub.get('status', 'Waiting for response'),
                "judge_status": sub.get('status', 'Waiting for response'),
            })
            
            if note:
                # Use just the note for a cleaner platform response preview
                if add_notification(contest_db_id, note, issue_id=issue_id):
                    print(f"  🔔 Recorded new review: {title}")
            elif status != 'pending':
                msg = f"Status changed to {status.upper()}"
                if add_notification(contest_db_id, msg, issue_id=issue_id):
                    print(f"  🔔 Recorded status update: {title}")
                    
    except Exception as e:
        print(f"[Guardian] ❌ Response check failed: {e}")





def status(token: Optional[str], session: Optional[requests.Session]) -> dict:
    """Return a JSON-serialisable status dict."""
    is_authed = bool(token)
    connected = False

    if is_authed and session:
        try:
            resp = session.get(
                f"{GUARDIAN_API}/api/contests", params={"page": 1, "limit": 1}, timeout=5
            )
            connected = resp.status_code == 200
        except Exception:
            pass

    cached = _load_cached_token()
    return {
        "platform": "guardian",
        "connected": connected,
        "authenticated": is_authed,
        "auth_method": "email_password" if GUARDIAN_EMAIL else "none",
        "email": GUARDIAN_EMAIL or "(not set)",
        "token_cached": bool(cached),
        "cache_file": str(TOKEN_CACHE),
    }


# ── Entry Point ───────────────────────────────────────────────────────────────

# ── Platform Submission ───────────────────────────────────────────────────────

def submit_finding_to_platform(issue: dict) -> str:
    """
    Submit a finding to Guardian Defender via their REST API.
    Returns the platform issue ID (e.g., 'guardian-sub-abc123').
    """
    print(f"[Guardian] 🚀 Submitting issue '{issue.get('title')}' to Guardian Defender...")

    token = login()
    if not token:
        print("[Guardian] ❌ Cannot submit — authentication failed.")
        return None

    session = _make_session(token)

    contest = get_contest(issue["contest_id"])
    if not contest:
        print(f"[Guardian] ❌ Contest {issue['contest_id']} not found in DB.")
        return None

    platform_cid = contest.get("platform_contest_id", "").replace("guardian-", "")
    if not platform_cid:
        print("[Guardian] ❌ Could not extract platform contest ID.")
        return None

    title = issue.get("title", "Security Finding")
    report = issue.get("submission_report") or ""
    poc = (issue.get("poc_code") or "").strip()
    description = issue.get("description", "")

    if report:
        body = report
    else:
        body = description
        if poc:
            body += f"\n\n## Proof of Concept\n\n```solidity\n{poc}\n```"

    severity = issue.get("severity", "Medium")

    payload = {
        "title": title,
        "description": body,
        "severity": severity.lower(),
    }
    if poc:
        payload["proofOfConcept"] = poc

    submit_url = f"{GUARDIAN_API}/api/contests/{platform_cid}/submissions"

    try:
        resp = session.post(submit_url, json=payload, timeout=30)
        resp.raise_for_status()

        res_data = resp.json()
        # Try multiple possible ID fields from the response
        sub_id = (
            res_data.get("id")
            or res_data.get("_id")
            or res_data.get("submissionId")
            or (res_data.get("data", {}) or {}).get("id")
            or (res_data.get("data", {}) or {}).get("_id")
        )

        if not sub_id:
            import uuid
            sub_id = f"guardian-unknown-{uuid.uuid4().hex[:8]}"

        print(f"[Guardian] ✅ Submission successful. Platform ID: {sub_id}")
        return f"guardian-{sub_id}"

    except requests.HTTPError as e:
        status_code = e.response.status_code if e.response else "?"
        body_text = e.response.text[:300] if e.response else "no body"
        print(f"[Guardian] ❌ Submission API error (HTTP {status_code}): {body_text}")
        return None
    except Exception as e:
        print(f"[Guardian] ❌ Failed to submit to Guardian: {e}")
        return None


def main():
    parser = argparse.ArgumentParser(description="Guardian Defender Bot")
    parser.add_argument("--sync", action="store_true", help="Sync contests from Guardian into DB")
    parser.add_argument("--check-responses", type=int, metavar="CONTEST_DB_ID", help="Check for judge responses in a contest")
    parser.add_argument("--join", type=int, metavar="CONTEST_DB_ID", help="Manually join a specific contest")
    parser.add_argument("--status", action="store_true", help="Print auth + connection status as JSON")
    args = parser.parse_args()

    token = login()
    session = _make_session(token) if token else None

    if args.status:
        print(json.dumps(status(token, session), indent=2))
    elif args.sync:
        if not session:
            print("[Guardian] ❌ Cannot sync — login failed. Check your credentials in .env")
            sys.exit(1)
        sync(session)
    elif args.check_responses:
        if not session:
            print("[Guardian] ❌ Cannot check responses — login failed.")
            sys.exit(1)
        check_responses(session, args.check_responses)
    elif args.join is not None:
        if not session:
            print("[Guardian] ❌ Cannot join — login failed.")
            sys.exit(1)
        success = join(session, args.join)
        if success:
            mark_joined(args.join)
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
