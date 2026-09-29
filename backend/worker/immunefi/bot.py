"""
Immunefi Bot
------------
Fetches Audit Competition data from immunefi.com (public, no auth required).

Immunefi uses Next.js App Router with React Server Components (RSC streaming).
There is NO __NEXT_DATA__ block. Instead, page data is streamed inline via
`self.__next_f.push(...)` script tags. We parse these to extract the `bounties`
array which contains all competition entries.

Authentication (Firebase) is only used for actions that require a logged-in
user: checking submission responses.

Usage:
  python3 -m worker.immunefi.bot --sync    # Discover & upsert contests
  python3 -m worker.immunefi.bot --status  # Print auth + connection info
"""
import sys
import re
import json
import argparse
import html as html_lib
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Optional, List

import requests
import redis as redis_lib
import os
from urllib.parse import urlsplit

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from shared.config import IMMUNEFI_EMAIL, IMMUNEFI_PASSWORD, CODEX_DIR
from shared.db import (
    upsert_contest, get_bot_id, log_sync,
    get_contest, mark_joined, get_contest_by_platform_id,
    get_trufy_scan_for_contest, has_active_or_finished_trufy_scan,
    add_notification, upsert_issue
)

# ── Constants ─────────────────────────────────────────────────────────────────

IMMUNEFI_BASE       = "https://immunefi.com"
IMMUNEFI_BUGS_BASE  = "https://bugs.immunefi.com"
PLATFORM            = "immunefi"
TOKEN_CACHE         = CODEX_DIR / "immunefi_token.json"
COMPLETED_CAP       = 5   # max completed contests to keep in DB

# Firebase Web API key — this is the public client-side key embedded in the
# Immunefi web app. It is intentionally public (not a secret).
_FIREBASE_API_KEY = "AIzaSyDRX6BXCA75DsKq9GQIknfGCMYRKHPAP4c"

# Community-maintained snapshot of Immunefi competitions (updated automatically)
# This is the most reliable source as the live site uses client-side rendering.
_SNAPSHOT_URL = (
    "https://raw.githubusercontent.com/pratraut/"
    "Immunefi-Bug-Bounty-Programs-Snapshots/main/audit_competitions.json"
)

# Slugs/keywords that indicate non-Audit Competition types to exclude
_EXCLUDE_PATTERNS = (
    "attackathon", "bug-bounty-comp", "bug-bounty-competition",
    "invite-only", "-iop", "iop-",
)

# Endpoint for the researcher's submissions on bugs.immunefi.com.
IMMUNEFI_SUBMISSIONS_URL = "https://bugs.immunefi.com/api/me/submitted-reports"


_redis_client = None

def _get_redis():
    global _redis_client
    if _redis_client is None:
        # Default matches docker-compose (redis runs on 6381), so a standalone run
        # without REDIS_URL set still reaches the same broker the worker uses.
        url = os.environ.get("REDIS_URL", "redis://redis:6381/0")
        _redis_client = redis_lib.Redis.from_url(url, decode_responses=True)
    return _redis_client

# ── Authentication (used only for submission check / join) ────────────────────

def _load_cached_token() -> Optional[str]:
    """Return a cached Firebase idToken if it has not yet expired. None otherwise."""
    if not TOKEN_CACHE.exists():
        return None
    try:
        data = json.loads(TOKEN_CACHE.read_text())
        token = data.get("token")
        expires_at = data.get("expires_at")
        if token and expires_at:
            exp = datetime.fromisoformat(expires_at)
            now = datetime.utcnow()
            if now < exp:
                remaining_min = (exp - now).total_seconds() / 60
                print(f"[Immunefi] 🔑 Using cached token (expires in {remaining_min:.0f} min)")
                return token
            print("[Immunefi] 🔄 Cached token expired — re-logging in")
    except Exception as e:
        print(f"[Immunefi] ⚠️  Could not read token cache: {e}")
    return None

def _save_token(token: str, expires_in_seconds: int = 3600):
    """Cache the idToken with a real expiry. Firebase ID tokens live ~1 hour."""
    TOKEN_CACHE.parent.mkdir(parents=True, exist_ok=True)
    # Refresh 60s early so we never present a token in its final moments of validity.
    safe_seconds = max(0, expires_in_seconds - 60)
    expires_at = datetime.utcnow() + timedelta(seconds=safe_seconds)
    TOKEN_CACHE.write_text(json.dumps({
        "token": token,
        "obtained_at": datetime.utcnow().isoformat(),
        "expires_at": expires_at.isoformat(),
    }))
    print("[Immunefi] 💾 Token cached.")

def login() -> Optional[str]:
    """
    Authenticate with Immunefi via Firebase REST API.
    Returns an idToken (JWT). Credentials are only required for actions
    like checking submissions or joining — NOT for contest discovery.
    """
    cached = _load_cached_token()
    if cached:
        return cached

    if not IMMUNEFI_EMAIL or not IMMUNEFI_PASSWORD:
        print("[Immunefi] ❌ Missing credentials (IMMUNEFI_EMAIL / IMMUNEFI_PASSWORD).")
        return None

    print(f"[Immunefi] 🔐 Logging in as {IMMUNEFI_EMAIL} via Firebase...")
    try:
        resp = requests.post(
            f"https://identitytoolkit.googleapis.com/v1/accounts:signInWithPassword"
            f"?key={_FIREBASE_API_KEY}",
            json={
                "email": IMMUNEFI_EMAIL,
                "password": IMMUNEFI_PASSWORD,
                "returnSecureToken": True,
            },
            timeout=15,
        )
        if resp.status_code == 200:
            body = resp.json()
            token = body.get("idToken")
            if token:
                try:
                    expires_in = int(body.get("expiresIn", "3600"))
                except (ValueError, TypeError):
                    expires_in = 3600
                _save_token(token, expires_in)
                print("[Immunefi] ✅ Login successful.")
                return token
            print("[Immunefi] ❌ Login response missing idToken.")
        else:
            err = resp.json().get("error", {}).get("message", resp.text)
            print(f"[Immunefi] ❌ Login failed: {err}")
    except Exception as e:
        print(f"[Immunefi] ❌ Login error: {e}")
    return None

def _make_session(token: Optional[str] = None) -> requests.Session:
    session = requests.Session()
    session.headers.update({
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
        "Accept": "application/json, text/html, */*",
    })
    if token:
        session.headers["Authorization"] = f"Bearer {token}"
    return session

# ── Data Fetching (Public — no auth) ─────────────────────────────────────────

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

def _clean_text(value) -> str:
    return str(value or "").strip()

def _build_description(comp: dict, status: str) -> str:
    """
    Immunefi snapshot entries do not expose a plain description field. The
    platform-provided contest overview lives in boosted intro markdown fields.
    Prefer the project/program overview, then append the status-specific
    platform note when it adds distinct information.
    """
    base = (
        _clean_text(comp.get("description")) or
        _clean_text(comp.get("boostedIntroLive")) or
        _clean_text(comp.get("boostedIntroStartingIn"))
    )

    status_note = ""
    if status == "judging":
        status_note = _clean_text(comp.get("boostedIntroEvaluating"))
    elif status == "completed":
        status_note = _clean_text(comp.get("boostedIntroFinished"))

    parts = []
    if base:
        parts.append(base)
    if status_note and status_note != base:
        parts.append(f"### Platform Status\n\n{status_note}")

    return "\n\n".join(parts)

def _immunefi_metadata(comp: dict) -> dict:
    keys = (
        "technologies",
        "tags",
        "proofOfConceptType",
        "rewardsToken",
        "sourceLinesOfCode",
        "evaluationEndDate",
        "kyc",
        "immunefiStandard",
        "boostedSummaryReport",
        "contentfulId",
    )
    return {key: comp.get(key) for key in keys if comp.get(key) not in (None, "", [], {})}

def _normalize_github_url(url: str) -> Optional[str]:
    parsed = urlsplit(url.strip().rstrip(".,;)\\"))
    if parsed.netloc.lower() != "github.com":
        return None

    parts = [part for part in parsed.path.split("/") if part]
    if len(parts) < 2:
        return None

    owner = parts[0]
    repo = re.split(r"(?:\\n|\\\\|%5C)", parts[1], maxsplit=1)[0].removesuffix(".git")
    if not owner or not repo:
        return None
    return f"https://github.com/{owner}/{repo}"

def _map_status(comp: dict, now: Optional[datetime] = None) -> str:
    """Normalize Immunefi competition states to our standard statuses."""
    now = now or datetime.utcnow()

    # Primary: auditCompetitionStatus is the canonical Immunefi field
    api_state = str(
        comp.get("auditCompetitionStatus") or
        comp.get("state") or
        comp.get("status") or ""
    ).upper()

    if api_state in ("FINISHED", "ENDED", "COMPLETED", "CLOSED", "CANCELLED"):
        return "completed"
    if api_state == "EVALUATING":
        return "judging"
    if api_state in ("ACTIVE", "ONGOING"):
        return "active"

    # Fallback: derive from dates
    # Immunefi uses launchDate for start, endDate for end
    starts_at = _parse_datetime(
        comp.get("launchDate") or comp.get("startDate") or comp.get("start_date")
    )
    ends_at = _parse_datetime(
        comp.get("endDate") or comp.get("end_date")
    )

    if starts_at and starts_at > now:
        return "upcoming"
    if ends_at and ends_at <= now:
        return "completed"
    if starts_at and starts_at <= now and (not ends_at or ends_at > now):
        return "active"

    # Unknown api_state AND no usable dates. We deliberately DO NOT default to
    # "completed" here: a mis-classified completed contest gets capped/hidden and
    # the bot would silently miss a contest it could have joined. "upcoming" keeps
    # it visible and harmless (it won't auto-join until it actually goes active).
    print(f"[Immunefi] ⚠️  Could not determine status for '{comp.get('slug') or comp.get('id')}' "
          f"(api_state={api_state or 'n/a'}); defaulting to 'upcoming'.")
    return "upcoming"

def _walk_for_competitions(obj) -> List[dict]:
    """
    Recursively search a parsed JSON object for Immunefi competition entries.
    Confirmed field names from RSC payload: slug, auditCompetitionStatus,
    rewardsPool, launchDate, endDate, project, logo, inviteOnly.
    """
    results = []
    if isinstance(obj, list):
        for item in obj:
            results.extend(_walk_for_competitions(item))
    elif isinstance(obj, dict):
        keys = set(obj.keys())
        # A competition entry has 'slug' plus at least one date/pool field
        is_comp = (
            "slug" in keys and
            bool(keys.intersection({
                "auditCompetitionStatus", "rewardsPool", "launchDate",
                "endDate", "maxBounty", "vaultBalance"
            }))
        )
        if is_comp:
            results.append(obj)
        else:
            for v in obj.values():
                results.extend(_walk_for_competitions(v))
    return results

def fetch_all_contests(session: Optional[requests.Session] = None) -> List[dict]:
    """
    Fetch all Immunefi Audit Competitions.

    Primary source: community-maintained GitHub JSON snapshot
      (pratraut/Immunefi-Bug-Bounty-Programs-Snapshots)
    Fallback: live Immunefi listing page (slug scraping).
    No authentication required for either approach.
    """
    if session is None:
        session = _make_session()

    # ── Primary: GitHub snapshot ──────────────────────────────────────────────
    print("[Immunefi] Fetching competition data from community snapshot...")
    snapshot_success = False
    for attempt in range(3):
        try:
            snap_resp = session.get(_SNAPSHOT_URL, timeout=15)
            if snap_resp.status_code == 200:
                # The snapshot JSON might contain unescaped control characters in description fields,
                # so strict=False allows parsing it cleanly.
                data = json.loads(snap_resp.text, strict=False)
                if isinstance(data, list) and data:
                    print(f"[Immunefi] ✅ Snapshot returned {len(data)} entries.")
                    return data
            snapshot_success = True
            break
        except Exception as e:
            if attempt == 2:
                print(f"[Immunefi] ⚠️  Snapshot fetch failed after 3 attempts: {e}")
            else:
                print(f"[Immunefi] ⚠️  Snapshot attempt {attempt+1} failed: {e}. Retrying...")
                import time
                time.sleep(2)

    # ── Fallback: live listing page slug scraping ─────────────────────────────
    print("[Immunefi] Falling back to live page scraping...")
    html_text = ""
    for attempt in range(3):
        try:
            resp = session.get(f"{IMMUNEFI_BASE}/audit-competition/", timeout=20)
            resp.raise_for_status()
            html_text = resp.text
            break
        except Exception as e:
            if attempt == 2:
                print(f"[Immunefi] ❌ Failed to fetch listing page after 3 attempts: {e}")
                return []
            else:
                print(f"[Immunefi] ⚠️  Live page attempt {attempt+1} failed: {e}. Retrying...")
                import time
                time.sleep(2)

    slugs = list(dict.fromkeys(re.findall(
        r'/audit-competition/([^/"\'\s]+)/information/',
        html_text
    )))
    # Limit to first 20 to avoid excessive individual fetches in the fallback
    slugs = slugs[:20]
    print(f"[Immunefi] Fetching {len(slugs)} contest pages individually...")
    competitions = []
    for slug in slugs:
        comp = _fetch_single_contest(slug, session)
        if comp:
            competitions.append(comp)
    return competitions


def _fetch_single_contest(slug: str, session: requests.Session) -> Optional[dict]:
    """Fetch details for a single competition from its information page."""
    url = f"{IMMUNEFI_BASE}/audit-competition/{slug}/information/"
    try:
        resp = session.get(url, timeout=15)
        resp.raise_for_status()
    except Exception as e:
        print(f"[Immunefi] ⚠️  Could not fetch {slug}: {e}")
        return None

    match = re.search(
        r'<script[^>]+id=["\']__NEXT_DATA__["\'][^>]*>(.*?)</script>',
        resp.text, re.DOTALL | re.IGNORECASE
    )
    if match:
        try:
            data = json.loads(html_lib.unescape(match.group(1)))
            entries = _walk_for_competitions(data)
            if entries:
                return entries[0]
        except Exception:
            pass

    # Minimal stub from URL if parsing completely fails
    return {"slug": slug, "title": slug.replace("-", " ").title()}

def fetch_contest_repos(slug: str, session: Optional[requests.Session] = None) -> List[str]:
    """
    Scrape GitHub repository URLs from the competition's public scope page.
    Returns a de-duplicated list of unique repo roots.
    No authentication required.
    """
    if session is None:
        session = _make_session()

    url = f"{IMMUNEFI_BASE}/audit-competition/{slug}/scope/"
    try:
        resp = session.get(url, timeout=15)
        if resp.status_code != 200:
            return []
        raw_urls = re.findall(r'https://github\.com/[^\s"\'<\)]+', resp.text)
        # Normalize to repo roots (org/repo only, strip sub-paths)
        roots = set()
        for u in raw_urls:
            normalized = _normalize_github_url(u)
            if normalized:
                roots.add(normalized)
        return sorted(roots)
    except Exception as e:
        print(f"[Immunefi] ⚠️  Could not fetch scope for {slug}: {e}")
        return []

def _normalize(comp: dict, now: Optional[datetime] = None) -> Optional[dict]:
    """
    Normalize a raw Immunefi competition entry into our standard contest params dict.
    Schema (from pratraut/Immunefi-Bug-Bounty-Programs-Snapshots):
      id (slug), project (title), maximum_reward, logo, launchDate, endDate.
    Returns None if this entry should be excluded.
    """
    # The snapshot uses 'id' as the slug; the live page uses 'slug'
    slug = comp.get("id") or comp.get("slug") or ""
    if not slug:
        return None

    # Exclude non-Audit Competition types by slug/title keywords
    slug_lower = slug.lower()
    if any(pat in slug_lower for pat in _EXCLUDE_PATTERNS):
        return None

    title = comp.get("project") or comp.get("title") or slug.replace("-", " ").title()
    title_lower = title.lower()
    # Also filter by title
    if "attackathon" in title_lower or "bug bounty comp" in title_lower:
        return None

    status = _map_status(comp, now)

    starts_at = comp.get("launchDate") or comp.get("startDate") or comp.get("date")
    ends_at   = comp.get("endDate")

    # Snapshot uses maximum_reward; live API may use rewardsPool or rewardPool
    reward_pool = 0.0
    for key in ("maximum_reward", "rewardsPool", "rewardPool", "maxBounty"):
        val = comp.get(key)
        if val is not None:
            try:
                reward_pool = float(str(val).replace(",", "").replace("$", ""))
                break
            except (ValueError, TypeError):
                pass

    platform_url = f"{IMMUNEFI_BASE}/audit-competition/{slug}/information/"
    logo_url = comp.get("logo") or ""
    # Snapshot logos are protocol-relative (//...), normalize to https
    if logo_url.startswith("//"):
        logo_url = "https:" + logo_url

    metadata = _immunefi_metadata(comp)
    reward_token = str(comp.get("rewardsToken") or "USDC").upper()

    return {
        "slug":                slug,
        "platform_contest_id": f"immunefi-{slug}",
        "title":               title,
        "platform_url":        platform_url,
        "status":              status,
        "starts_at":           starts_at,
        "ends_at":             ends_at,
        "prize_pool":          reward_pool,
        "token":               reward_token,
        "synced_from":         PLATFORM,
        "is_finalized":        status == "completed",
        "description":         _build_description(comp, status),
        "logo_url":            logo_url,
        "metadata":            metadata,
    }

# ── Core Commands ─────────────────────────────────────────────────────────────

def sync(session: Optional[requests.Session] = None):
    """
    Main sync function. Discovers all Audit Competitions and upserts them.
    Queues Trufy scan jobs for joined contests with a repo.
    No credentials needed — all discovery is public.
    """
    print("[Immunefi] Starting sync...")
    if session is None:
        session = _make_session()

    bot_id = get_bot_id(PLATFORM)
    if not bot_id:
        msg = "No active Immunefi bot found in DB"
        print(f"[Immunefi] ❌ {msg}")
        log_sync(PLATFORM, 0, 0, "error", msg)
        return

    raw_comps = fetch_all_contests(session)
    now = datetime.utcnow()

    normalized = []
    for comp in raw_comps:
        n = _normalize(comp, now)
        if n:
            normalized.append(n)

    # Sort by start date descending; cap completed contests
    normalized.sort(key=lambda c: c.get("starts_at") or "", reverse=True)
    selected, completed_seen = [], 0
    for c in normalized:
        if c["status"] == "completed":
            # Always keep a contest already tracked locally that is transitioning
            # INTO completed (e.g. judging -> completed), otherwise it would stay
            # stuck at its old status in the UI forever. The cap applies only to
            # brand-new historical contests and ones already stored as completed.
            db_row = get_contest_by_platform_id(c["platform_contest_id"])
            transitioning = bool(db_row) and db_row.get("status") != "completed"
            if not transitioning:
                if completed_seen >= COMPLETED_CAP:
                    continue
                completed_seen += 1
        selected.append(c)

    print(f"[Immunefi] {len(raw_comps)} entries fetched, {len(selected)} selected for sync.")

    new_count = 0
    for params in selected:
        slug = params["slug"]
        params["bot_id"] = bot_id

        # Fetch repo URLs from the public scope page
        repo_urls = fetch_contest_repos(slug, session)
        params["code_url"] = json.dumps(repo_urls)

        db_contest = get_contest_by_platform_id(params["platform_contest_id"])

        # Override status to 'auditing' if Trufy scan in progress
        local_status = params["status"]
        if db_contest:
            scan = get_trufy_scan_for_contest(db_contest["id"])
            if scan and scan.get("status") in ("pending", "scanning"):
                local_status = "auditing"
        params["status"] = local_status

        is_new = upsert_contest(params)
        if is_new:
            new_count += 1

        if not db_contest:
            db_contest = get_contest_by_platform_id(params["platform_contest_id"])

        if db_contest:
            start_dt = _parse_datetime(params.get("starts_at"))
            end_dt   = _parse_datetime(params.get("ends_at"))
            has_started = not start_dt or start_dt <= now
            is_ended    = bool(end_dt and end_dt <= now)

            has_joined = db_contest.get("has_joined", False)

            # Auto-audit: if joined, contest started, has a repo, and no scan yet
            if has_joined and repo_urls and has_started and not is_ended:
                if not has_active_or_finished_trufy_scan(db_contest["id"]):
                    try:
                        _get_redis().lpush("jobs", json.dumps({
                            "action": "trufy_scan",
                            "contest_id": db_contest["id"]
                        }))
                        print(f"  🤖 Trufy scan queued for '{params['title']}'")
                    except Exception as re_err:
                        print(f"  ⚠️  Could not enqueue Trufy scan: {re_err}")

        label = "✨ New" if is_new else "↻  Updated"
        print(f"  {label}: {params['title']} [{params['status']}] | repos: {len(repo_urls)}")

    log_sync(PLATFORM, len(selected), new_count)
    print(f"[Immunefi] ✅ Sync done — {len(selected)} selected, {new_count} new.")

def join(session, contest_db_id: int) -> bool:
    """
    Join an Immunefi competition.
    Auto-join is intentionally disabled for Immunefi — the submission flow
    requires KYC acceptance and program-specific terms that must be done manually.
    This function is a no-op stub; joining is done by the researcher on the website.
    """
    contest = get_contest(contest_db_id)
    title = contest.get("title", f"#{contest_db_id}") if contest else f"#{contest_db_id}"
    print(f"[Immunefi] ℹ️  Auto-join not supported for Immunefi ('{title}'). "
          f"Please join manually at immunefi.com and the bot will pick up your submissions.")
    return False

# ── Submission / Judge-Response Sync ──────────────────────────────────────────

# Maps Immunefi submission verdict/state strings to our issue.status values.
# CONFIRM these keys against a real payload — the left-hand strings are best-effort.
_IMMUNEFI_VERDICT_TO_STATUS = {
    "accepted":     "verified",
    "confirmed":    "verified",
    "valid":        "verified",
    "resolved":     "verified",
    "paid":         "verified",
    "rejected":     "skipped",
    "invalid":      "skipped",
    "duplicate":    "skipped",
    "spam":         "skipped",
    "closed":       "skipped",
    "out_of_scope": "skipped",
    "pending":      "pending",
    "new":          "pending",
    "triaging":     "pending",
    "under_review": "pending",
    "in_review":    "pending",
}

def _existing_issue_verdict(platform_issue_id: str) -> Optional[str]:
    """Read the currently-stored judge_verdict so we only notify on real changes."""
    try:
        from shared.db import _conn
        with _conn() as cur:
            cur.execute(
                "SELECT judge_verdict FROM issues WHERE platform_issue_id = %s",
                (platform_issue_id,),
            )
            row = cur.fetchone()
            if not row:
                return None
            return row.get("judge_verdict") if isinstance(row, dict) else row[0]
    except Exception:
        return None

def _fetch_submissions(slug: str, session: requests.Session) -> List[dict]:
    """
    Fetch the researcher's submissions for one competition from bugs.immunefi.com.
    """
    try:
        resp = session.get(IMMUNEFI_SUBMISSIONS_URL, params={"project": slug}, timeout=20)
        if resp.status_code in (401, 403):
            print(f"[Immunefi] ⚠️  Submission fetch unauthorized ({resp.status_code}) — token may be expired.")
            return []
        resp.raise_for_status()
        data = resp.json()
    except Exception as e:
        print(f"[Immunefi] ⚠️  Could not fetch submissions for {slug}: {e}")
        return []

    # Responses are commonly wrapped; accept a bare list or a few common envelopes.
    if isinstance(data, list):
        return data
    if isinstance(data, dict):
        for key in ("submissions", "reports", "data", "items", "results", "nodes"):
            val = data.get(key)
            if isinstance(val, list):
                return val
    print("[Immunefi] ⚠️  Submission response shape not recognized — check _fetch_submissions.")
    return []

def _parse_submission(raw: dict, contest_db_id: int, slug: str) -> Optional[dict]:
    """
    Turn one raw Immunefi submission into an issue-upsert dict.

    ⚠️  The field names below are best-effort and MUST be confirmed against the real
    bugs.immunefi.com payload. Everything uses .get() so an unexpected/missing field
    degrades gracefully instead of crashing.
    """
    if not isinstance(raw, dict):
        return None

    sub_id = (
        raw.get("id") or raw.get("submissionId") or raw.get("reportId")
        or raw.get("number") or raw.get("uuid")
    )
    if sub_id is None:
        return None

    title = _clean_text(raw.get("title") or raw.get("name")) or f"Immunefi submission {sub_id}"

    severity_raw = str(raw.get("severity") or raw.get("impact") or "").strip().lower()
    severity = {
        "critical": "Critical", "high": "High", "medium": "Medium",
        "low": "Low", "informational": "Low", "info": "Low",
    }.get(severity_raw, "Medium")

    verdict_raw = str(
        raw.get("status") or raw.get("state") or raw.get("closedState") or raw.get("verdict") or ""
    ).strip().lower()
    mapped_status = _IMMUNEFI_VERDICT_TO_STATUS.get(verdict_raw, "pending")

    judge_verdict = _clean_text(
        raw.get("verdict") or raw.get("closedReason") or raw.get("resolution") or verdict_raw
    ) or "Waiting for response"

    return {
        "contest_id":        contest_db_id,
        "platform_issue_id": f"immunefi-{slug}-{sub_id}",
        "title":             title,
        "description":       _clean_text(raw.get("description") or raw.get("body") or ""),
        "severity":          severity,
        "classification":    "immunefi-submission",
        "status":            mapped_status,
        "confidence":        "",
        "judge_status":      verdict_raw or "pending",
        "judge_verdict":     judge_verdict,
    }

def check_responses(contest_db_id: int, force: bool = False):
    """
    Sync submitted reports and judge responses from bugs.immunefi.com.

    Requires authentication. Pulls the researcher's submissions for the contest,
    upserts each as an issue (preserving any already-verified PoC via the
    upsert_issue guards), and fires a notification when a judge verdict changes.

    `force` re-emits a notification even when the verdict text is unchanged.
    """
    contest = get_contest(contest_db_id)
    if not contest:
        print(f"[Immunefi] ❌ Contest {contest_db_id} not found.")
        return

    title = contest.get("title", str(contest_db_id))
    pid   = contest.get("platform_contest_id", "")
    slug  = pid.removeprefix("immunefi-")

    # Authentication required for submission fetch
    token = login()
    if not token:
        print(f"[Immunefi] ⚠️  Cannot check responses for '{title}' — auth failed.")
        return

    sess = _make_session(token)

    raw_subs = _fetch_submissions(slug, sess)
    if not raw_subs:
        print(f"[Immunefi] No submissions to sync for '{title}'.")
        return

    synced = 0
    for raw in raw_subs:
        issue = _parse_submission(raw, contest_db_id, slug)
        if not issue:
            continue

        prev_verdict = _existing_issue_verdict(issue["platform_issue_id"])
        issue_db_id = upsert_issue(issue)
        synced += 1

        new_verdict = issue["judge_verdict"]
        verdict_is_real = new_verdict and new_verdict != "Waiting for response"
        verdict_changed = new_verdict != prev_verdict
        if verdict_is_real and (verdict_changed or force):
            add_notification(
                contest_db_id,
                f"Immunefi judge update on '{issue['title']}': {new_verdict}",
                issue_db_id,
            )

    print(f"[Immunefi] ✅ Synced {synced} submission(s) for '{title}'.")

def status(token=None, session=None) -> dict:
    """Returns a dict describing connection + auth status."""
    connected    = False
    authenticated = False

    try:
        resp = requests.get(f"{IMMUNEFI_BASE}/audit-competition/", timeout=5)
        connected = resp.status_code == 200
    except Exception:
        pass

    if connected and IMMUNEFI_EMAIL and IMMUNEFI_PASSWORD:
        # Try loading cached token — avoids a live login on every status check
        cached = _load_cached_token()
        authenticated = cached is not None

    return {
        "platform":      PLATFORM,
        "connected":     connected,
        "authenticated": authenticated,
        "auth_method":   "firebase",
        "note": (
            "Using cached Firebase token." if authenticated else
            "Not authenticated. Will login on first authenticated action."
        ),
    }

# ── Entry Point ───────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Immunefi Bot")
    parser.add_argument("--sync",   action="store_true", help="Sync Audit Competitions")
    parser.add_argument("--status", action="store_true", help="Print connection status")
    args = parser.parse_args()

    sess = _make_session()

    if args.status:
        print(json.dumps(status(), indent=2))
    elif args.sync:
        sync(sess)
    else:
        parser.print_help()

if __name__ == "__main__":
    main()
