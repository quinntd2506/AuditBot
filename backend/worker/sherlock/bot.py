"""
Sherlock Audits Bot
-------------------
Fetches contest data from audits.sherlock.xyz.

Authentication (Server-Ready):
  - Uses SHERLOCK_SESSION_COOKIE in .env or updated via UI
  - Contest LISTING is fully public (no auth needed for read-only sync).
"""
import os
import sys
import json
import argparse
from typing import Optional, List
from datetime import datetime, timezone
from pathlib import Path

import requests
import redis as redis_lib

# Add bots/ to path so we can import shared modules
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from shared.config import SHERLOCK_SESSION_COOKIE
from shared.db import (
    upsert_contest, get_bot_id, log_sync, get_contest,
    get_contest_by_platform_id,
    get_trufy_scan_for_contest, has_active_or_finished_trufy_scan,
    add_notification, upsert_issue
)

SHERLOCK_API = "https://audits.sherlock.xyz"
PLATFORM = "sherlock"

REDIS_URL = os.getenv("REDIS_URL", "redis://redis:6381/0")
_redis = redis_lib.Redis.from_url(REDIS_URL, decode_responses=True)
REDIS_COOKIE_KEY = "config:sherlock_cookie"




# ── Authentication ────────────────────────────────────────────────────────────

def _parse_cookie_string(raw: str) -> dict:
    """Parse a 'key=val; key2=val2' cookie header string into a dict."""
    result = {}
    for part in raw.split(";"):
        part = part.strip()
        if "=" in part:
            k, v = part.split("=", 1)
            result[k.strip()] = v.strip()
    return result


def _get_runtime_cookie() -> str:
    """Get cookie from Redis runtime store (set via UI), falls back to .env value."""
    try:
        runtime = _redis.get(REDIS_COOKIE_KEY)
        if runtime:
            return runtime
    except Exception:
        pass
    return SHERLOCK_SESSION_COOKIE


def build_session() -> requests.Session:
    """
    Build a requests.Session with Sherlock auth.
    Uses runtime cookie from Redis or falls back to .env cookie.
    """
    session = requests.Session()
    session.headers.update({
        "Accept": "application/json",
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
        "Origin": "https://audits.sherlock.xyz",
        "Referer": "https://audits.sherlock.xyz/",
    })

    # Use runtime cookie (from UI) or .env fallback
    cookie_str = _get_runtime_cookie()
    if cookie_str:
        source = "Redis" if _redis.get(REDIS_COOKIE_KEY) else ".env"
        print(f"[Sherlock] 🍪 Using session cookie from {source}")
        cookies = _parse_cookie_string(cookie_str)
        session.cookies.update(cookies)
        return session

    print("[Sherlock] ℹ️  No auth — read-only mode")
    return session



def _check_cookie_valid(session: requests.Session) -> bool:
    """Probe an auth-gated endpoint to verify cookie freshness."""
    if not session.cookies:
        return False
    try:
        # /api/profile requires auth, so a 200 + an identity field proves the
        # cookie is live. /api/auditors may be public and gives false positives.
        r = session.get(f"{SHERLOCK_API}/api/profile", timeout=6)
        if r.status_code != 200:
            return False
        data = r.json()
        return bool(data.get("handle") or data.get("github_handle"))
    except Exception:
        return False


# ── Data Fetching ─────────────────────────────────────────────────────────────

def _map_status(raw_status: str, starts_ts: Optional[int] = None) -> str:
    if starts_ts:
        now = datetime.now(timezone.utc).timestamp()
        if now < starts_ts:
            return "upcoming"

    if raw_status in ("LIVE", "RUNNING"):
        return "active"
    if raw_status == "CREATED":
        return "upcoming"
    if raw_status in ("SHERLOCK_JUDGING", "ESCALATING", "JUDGING_FINISHED", "READY_FOR_PAYOUT"):
        return "judging"
    
    # FINISHED, COMPLETED, etc.
    return "completed"


def fetch_contests_page(session: requests.Session, page: int = 1) -> dict:
    resp = session.get(f"{SHERLOCK_API}/api/contests", params={"page": page}, timeout=10)
    resp.raise_for_status()
    return resp.json()


def fetch_contest_detail(session: requests.Session, contest_id: int) -> Optional[dict]:
    try:
        resp = session.get(f"{SHERLOCK_API}/api/contests/{contest_id}", timeout=10)
        resp.raise_for_status()
        return resp.json()
    except Exception:
        return None


# ── Core Commands ─────────────────────────────────────────────────────────────

def sync_submissions(session: requests.Session, contest_db_id: int, platform_id: int):
    """Fetch user's findings for a specific Sherlock contest."""
    try:
        resp = session.get(f"{SHERLOCK_API}/api/contests/{platform_id}/my-submissions", timeout=10)
        if resp.status_code != 200:
            print(f"[Sherlock] ❌ Failed to fetch submissions: {resp.status_code}")
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
            note = f.get("judging_notes") or f.get("judgeComment") or f.get("comment") or f.get("reason")
            issue_id = upsert_issue({
                "contest_id": contest_db_id,
                "platform_issue_id": f"sherlock-{platform_id_f}",
                "title": f.get("title", "Sherlock Finding"),
                "description": f.get("description", ""),
                "severity": f.get("severity") or "Unknown",
                "status": "submitted",
                "judge_verdict": verdict,
                "judge_status": verdict,
            })
            if note:
                add_notification(contest_db_id, str(note), issue_id=issue_id)
    except Exception as e:
        print(f"[Sherlock] ⚠️  Submission sync failed for contest {platform_id}: {e}")

def sync(session: requests.Session):
    """Fetch Sherlock contests and upsert them into the local DB."""
    print(f"[Sherlock] Starting sync...")

    bot_id = get_bot_id("sherlock")
    if not bot_id:
        msg = "No active Sherlock bot found in DB"
        print(f"[Sherlock] ❌ {msg}")
        log_sync(PLATFORM, 0, 0, "error", msg)
        return

    items = []
    for attempt in range(3):
        try:
            data = fetch_contests_page(session, page=1)
            items = data.get("items", [])
            break
        except Exception as e:
            if attempt == 2:
                msg = f"Failed to fetch contest list after 3 attempts: {e}"
                print(f"[Sherlock] ❌ {msg}")
                log_sync(PLATFORM, 0, 0, "error", msg)
                return
            print(f"[Sherlock] ⚠️  Attempt {attempt+1} failed: {e}. Retrying...")
            import time
            time.sleep(2)
    print(f"[Sherlock] Fetched {len(items)} contests")

    # Sort items by date descending
    items.sort(key=lambda x: x.get("starts_at") or 0, reverse=True)

    # Fetch github_handle for private repo construction
    github_handle = ""
    try:
        profile_resp = session.get(f"{SHERLOCK_API}/api/profile", timeout=10)
        if profile_resp.ok:
            github_handle = profile_resp.json().get("github_handle", "") or ""
            if not github_handle:
                print("[Sherlock] ⚠️  Profile has no 'github_handle' — private repo URLs will be incomplete.")
    except Exception as e:
        print(f"[Sherlock] ⚠️  Could not fetch profile for github_handle: {e}")
    new_count = 0
    now = datetime.now(timezone.utc).timestamp()
    normalized = []

    for it in items:
        status = _map_status(it.get("status", "FINISHED"), it.get("starts_at"))
        normalized.append((it, status))

    # History capping: a freshly created bot shouldn't show an empty dashboard, so
    # when there are NO active contests we surface the most recent completed ones to
    # populate the UI. When something IS active, skip completed contests entirely —
    # the bot auto-joins live work and old contests have no further effect. (Contests
    # awaiting judges sit at status 'judging', not 'completed', so this doesn't hide
    # anything you're still waiting on.)
    MAX_COMPLETED_HISTORY = 5
    has_active = any(status == "active" for _, status in normalized)
    completed_seen = 0

    for it, status in normalized:
        contest_id = it["id"]
        starts_ts = it.get("starts_at")
        ends_ts = it.get("ends_at")
        has_started = not starts_ts or now >= starts_ts

        # Look up the locally-tracked row up front so capping can distinguish a
        # brand-new historical contest from one that is transitioning into 'completed'.
        db_contest = get_contest_by_platform_id(f"sherlock-{contest_id}")
        db_has_joined = db_contest.get("has_joined", False) if db_contest else False

        # 1. History Capping (completed contests only)
        # A contest already tracked locally whose status is changing (e.g.
        # judging/auditing/active -> completed) MUST be persisted, or it stays stuck
        # at its old status in the UI forever. So capping applies only to completed
        # contests that are brand-new (never tracked) or already stored as completed —
        # a real transition into completed always falls through and gets written.
        if status == "completed":
            prior_status = db_contest.get("status") if db_contest else None
            transitioning_to_completed = db_contest is not None and prior_status != "completed"
            if not transitioning_to_completed:
                if has_active:
                    continue
                completed_seen += 1
                if completed_seen > MAX_COMPLETED_HISTORY:
                    continue

        # 2. Strict End Date Check
        is_ended = False
        if ends_ts and now > ends_ts:
            is_ended = True

        # Fetch detail for metadata
        repo_url = ""
        description = ""
        if status != "completed":
            detail = fetch_contest_detail(session, contest_id)
            if detail:
                repo_name = detail.get("template_repo_name") or detail.get("repo")
                if repo_name:
                    if db_has_joined:
                        if github_handle:
                            repo_name = f"{repo_name}-{github_handle}"
                        else:
                            print(f"  ⚠️  '{it.get('title','')}' joined but no github_handle — using base repo; Trufy may fail on the private repo")
                    repo_url = f"https://github.com/{repo_name}"
                description = detail.get("short_description") or detail.get("description") or ""

        # We skip the detail fetch for completed contests, so `description` is "" for
        # them. Don't let that empty value overwrite a description already stored.
        if not description and db_contest and db_contest.get("description"):
            description = db_contest.get("description")

        params = {
            "bot_id": bot_id,
            "platform_contest_id": f"sherlock-{contest_id}",
            "title": it.get("title", ""),
            "code_url": json.dumps([repo_url] if repo_url else []),
            "platform_url": f"https://audits.sherlock.xyz/contests/{contest_id}",
            "status": status,
            "description": description,
            "prize_pool": it.get("prize_pool", 0),
            "token": it.get("token", "USDC"),
            "logo_url": it.get("logo_url", ""),
            "synced_from": PLATFORM,
            "starts_at": datetime.fromtimestamp(starts_ts, timezone.utc).isoformat() if starts_ts else None,
            "ends_at": datetime.fromtimestamp(ends_ts, timezone.utc).isoformat() if ends_ts else None,
            "is_finalized": status == "completed",
        }

        # 3. Enrollment Sync
        # We no longer force `has_joined` to False if it's missing from profile_resp,
        # thereby preserving whatever state exists in the database.

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
            db_has_joined = db_contest.get("has_joined", False) if db_contest else False

        if db_contest:
            if (
                not db_has_joined
                and status == "active"
                and has_started
                and not is_ended
                and not db_contest.get("skip_autojoin")
            ):
                try:
                    _redis.lpush("jobs", json.dumps({
                        "action": "join_contest",
                        "contest_id": db_contest["id"],
                        "platform": PLATFORM
                    }))
                    print(f"  ⚡ Join queued for '{it.get('title', '')}'")
                except Exception as re:
                    print(f"  ⚠️  Could not enqueue join: {re}")

            # Sync submissions
            if db_has_joined:
                sync_submissions(session, db_contest["id"], contest_id)

            # 4. Auto-Audit Logic
            if db_has_joined and repo_url:
                if not has_started:
                    print(f"  ⏳ Joined but not started yet; scan deferred for '{it['title']}'")
                elif is_ended:
                    print(f"  ⏭️  Contest ended; Trufy scan skipped for '{it['title']}'")
                elif not has_active_or_finished_trufy_scan(db_contest["id"]):
                    try:
                        _redis.lpush("jobs", json.dumps({
                            "action": "trufy_scan",
                            "contest_id": db_contest["id"]
                        }))
                        print(f"  🤖 Trufy scan queued for '{it['title']}'")
                    except:
                        pass

        if is_new:
            new_count += 1
            print(f"  ✨ New: {it['title']} [{local_status}]")
        else:
            print(f"  ↻  Updated: {it['title']} [{local_status}]")

    log_sync(PLATFORM, len(items), new_count)
    print(f"[Sherlock] ✅ Sync done — {len(items)} fetched, {new_count} new")


def join(session: requests.Session, contest_db_id: int) -> bool:
    """Automated Join Contest Logic.
    
    Handles contests with context questions (sponsor-answered pre-filled questions).
    Contests requiring KYC cannot be joined automatically — logs a human-readable
    warning so the user knows to sign up manually on Sherlock's website.
    """
    if not session.cookies:
        print("[Sherlock] ❌ Cannot join contest: Not authenticated.")
        return False
        
    contest = get_contest(contest_db_id)
    if not contest:
        print(f"[Sherlock] ❌ Contest with DB id={contest_db_id} not found")
        return False
        
    platform_id = contest["platform_contest_id"].replace("sherlock-", "")
    title = contest.get("title", f"ID:{contest_db_id}")

    # Fetch contest detail to check signup requirements
    try:
        detail_resp = session.get(f"{SHERLOCK_API}/api/contests/{platform_id}", timeout=10)
        detail = detail_resp.json() if detail_resp.ok else {}
    except Exception:
        detail = {}

    if not detail.get("allows_signups", True):
        print(f"[Sherlock] ℹ️  '{title}' is not open for signups yet.")
        return False

    # Build payload including any pre-answered context questions
    context_questions = detail.get("context_questions", [])
    # 1. Fetch profile to get handles/wallet
    print("[Sherlock] 👤 Fetching profile handle...")
    try:
        profile_resp = session.get(f"{SHERLOCK_API}/api/profile", timeout=10)
        profile = profile_resp.json() if profile_resp.ok else {}
        handle = profile.get("handle")
    except Exception as e:
        print(f"[Sherlock] ⚠️  Could not fetch profile: {e}")
        handle = None

    if not handle:
        print("[Sherlock] ❌ Cannot join: Handle not found in profile. Please ensure you are logged in correctly.")
        return False

    # 2. Prepare the join payload based on user's browser trace
    payload: dict = {
        "contest_id": int(platform_id),
        "handle": handle,
        "counts_towards_ranking": False  # Default to false to avoid complexities
    }
    
    print(f"[Sherlock] 🤝 Joining '{title}' (ID: {platform_id}) as @{handle}...")
    headers = {
        "Referer": f"https://audits.sherlock.xyz/contests/{platform_id}/join",
        "Content-Type": "application/json"
    }
    
    try:
        # Use the correct join-contest endpoint from browser trace
        # Increased timeout to 30s as Sherlock can be slow during enrollment
        resp = session.post(
            f"{SHERLOCK_API}/api/contests/join-contest", 
            json=payload, 
            headers=headers,
            timeout=30
        )
        
        if resp.status_code in (200, 201):
            print(f"[Sherlock] ✅ Successfully joined '{title}'!")
            return True
        elif resp.status_code == 400:
            body = resp.json() if resp.content else {}
            msg = body.get("message", str(body))
            if "already" in msg.lower() or "enrolled" in msg.lower():
                print(f"[Sherlock] ℹ️  Already joined '{title}'.")
                return True
            print(f"[Sherlock] ⚠️  Join 400: {msg}")
            return False
        else:
            print(f"[Sherlock] ⚠️  Join returned {resp.status_code}: {resp.text[:150]}")
            return False
    except Exception as e:
        print(f"[Sherlock] ❌ Error joining contest: {e}")
        return False



def status(session: requests.Session) -> dict:
    """Return a JSON-serialisable status dict."""
    cookie_count = len(session.cookies)
    is_authed = cookie_count > 0

    connected = False
    try:
        resp = session.get(f"{SHERLOCK_API}/api/contests", params={"page": 1}, timeout=5)
        connected = resp.status_code == 200
    except Exception:
        pass

    cookie_valid = _check_cookie_valid(session) if is_authed else False

    runtime_cookie = None
    try:
        runtime_cookie = _redis.get(REDIS_COOKIE_KEY)
    except Exception:
        pass

    auth_method = "none"
    if runtime_cookie: auth_method = "runtime_cookie"
    elif SHERLOCK_SESSION_COOKIE: auth_method = "env_cookie"


    return {
        "platform": "sherlock",
        "connected": connected,
        "authenticated": is_authed,
        "cookie_valid": cookie_valid,
        "cookie_source": "redis" if runtime_cookie else ("env" if SHERLOCK_SESSION_COOKIE else "none"),
        "auth_method": auth_method,
        "cookie_count": cookie_count,
        "read_only": not cookie_valid,
        "note": "Contest listing is public.",
    }



# ── Entry Point ───────────────────────────────────────────────────────────────

def check_responses(contest_id: Optional[int] = None, force: bool = False):
    """Fetch Sherlock submissions and judge responses for a contest."""
    print(f"[Sherlock] Checking responses for contest ID: {contest_id if contest_id else 'ALL'} (force={force})")
    if not contest_id:
        return

    contest = get_contest(contest_id)
    if not contest:
        print(f"[Sherlock] ❌ Contest {contest_id} not found in DB")
        return
    if contest.get("is_finalized") and not force:
        print(f"[Sherlock] 💤 Contest {contest_id} is finalized. Skipping response check.")
        return

    platform_id = str(contest.get("platform_contest_id", "")).replace("sherlock-", "")
    if not platform_id:
        print(f"[Sherlock] ❌ Could not extract platform id for contest {contest_id}")
        return

    session = build_session()
    sync_submissions(session, contest_id, platform_id)


# ── Platform Submission ───────────────────────────────────────────────────────

def submit_finding_to_platform(issue: dict) -> str:
    """
    Submit a finding to Sherlock by creating a GitHub Issue
    on the user's private audit repository.
    Returns the platform issue ID (e.g., 'sherlock-gh-42').
    """
    from shared.config import GITHUB_TOKEN

    print(f"[Sherlock] 🚀 Submitting issue '{issue.get('title')}' to Sherlock via GitHub...")

    if not GITHUB_TOKEN:
        print("[Sherlock] ❌ No GITHUB_TOKEN configured in .env — cannot submit.")
        return None

    contest = get_contest(issue["contest_id"])
    if not contest:
        print(f"[Sherlock] ❌ Contest {issue['contest_id']} not found in DB.")
        return None

    # Extract the private repo path from code_url (e.g., "sherlock-audit/...-quinntd2506")
    code_url_raw = contest.get("code_url", "[]")
    try:
        urls = json.loads(code_url_raw) if isinstance(code_url_raw, str) else code_url_raw
    except (json.JSONDecodeError, TypeError):
        urls = []

    repo_full_name = None
    for url in urls:
        if "github.com/" in url:
            # Extract "owner/repo" from "https://github.com/owner/repo"
            repo_full_name = url.split("github.com/")[-1].strip("/")
            break

    if not repo_full_name:
        print(f"[Sherlock] ❌ No GitHub repo URL found in code_url for contest {contest.get('title')}")
        return None

    # Build the issue body from submission_report or description + PoC
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

    # NOTE: We intentionally do NOT send labels. GitHub rejects the entire
    # issue-creation request with 422 (Validation Failed) if a label name does
    # not already exist in the repo, and Sherlock's provisioned repos don't ship
    # with severity labels. If you need severity on the issue, fold it into the
    # title or body instead of using a label.

    # POST to GitHub Issues API
    api_url = f"https://api.github.com/repos/{repo_full_name}/issues"
    headers = {
        "Authorization": f"Bearer {GITHUB_TOKEN}",
        "Accept": "application/vnd.github.v3+json",
    }
    payload = {
        "title": title,
        "body": body,
    }

    try:
        resp = requests.post(api_url, json=payload, headers=headers, timeout=30)
        resp.raise_for_status()

        gh_issue = resp.json()
        issue_number = gh_issue.get("number")
        print(f"[Sherlock] ✅ GitHub Issue #{issue_number} created on {repo_full_name}")
        return f"sherlock-gh-{issue_number}"

    except requests.HTTPError as e:
        status_code = e.response.status_code if e.response else "?"
        body_text = e.response.text[:300] if e.response else "no body"
        print(f"[Sherlock] ❌ GitHub API error (HTTP {status_code}): {body_text}")
        return None
    except Exception as e:
        print(f"[Sherlock] ❌ Failed to submit to GitHub: {e}")
        return None


def main():
    parser = argparse.ArgumentParser(description="Sherlock Audits Bot")
    parser.add_argument("--sync", action="store_true", help="Sync contests from Sherlock into DB")
    parser.add_argument("--join", type=int, metavar="CONTEST_DB_ID", help="Join a specific contest using auth")
    parser.add_argument("--status", action="store_true", help="Print auth + connection status as JSON")
    args = parser.parse_args()

    session = build_session()

    if args.status:
        print(json.dumps(status(session), indent=2))
    elif args.sync:
        sync(session)
    elif args.join is not None:
        join(session, args.join)
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
