from __future__ import annotations
import os
import json
import psycopg2
from psycopg2.extras import Json, RealDictCursor
from datetime import datetime, timezone
from typing import Optional

DATABASE_URL = os.environ.get("DATABASE_URL")

if not DATABASE_URL:
    raise RuntimeError("DATABASE_URL environment variable is not set")

class DBContextManager:
    def __enter__(self):
        try:
            self.conn = psycopg2.connect(DATABASE_URL)
            self.conn.autocommit = True
            self.cur = self.conn.cursor(cursor_factory=RealDictCursor)
            return self.cur
        except Exception as e:
            print(f"[Worker] Database connection failed: {e}")
            raise RuntimeError("Worker could not connect to database. Check credentials.")

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.cur.close()
        self.conn.close()

def _conn():
    return DBContextManager()

def get_bot_id(platform: str) -> Optional[int]:
    """Return the DB id of the first non-paused bot for the given platform."""
    with _conn() as cur:
        cur.execute("SELECT id FROM bots WHERE platform = %s AND status != 'paused' LIMIT 1", (platform,))
        row = cur.fetchone()
        return row["id"] if row else None

def _parse_skill_names(value) -> list[str]:
    if not value:
        return []
    if isinstance(value, list):
        raw = value
    else:
        try:
            parsed = json.loads(str(value))
            raw = parsed if isinstance(parsed, list) else [parsed]
        except Exception:
            raw = [str(value)]

    names: list[str] = []
    for item in raw:
        name = str(item).strip()
        if name and name not in names:
            names.append(name)
    return names

VALID_SKILL_PHASES = {"triage", "poc", "report"}
DEFAULT_SKILL_PHASES = ["triage", "poc", "report"]


def _parse_json_list(value, default: list[str] | None = None) -> list[str]:
    if value is None:
        return list(default or [])
    if isinstance(value, list):
        raw = value
    else:
        try:
            parsed = json.loads(str(value))
            raw = parsed if isinstance(parsed, list) else [parsed]
        except Exception:
            raw = [str(value)]

    items: list[str] = []
    for item in raw:
        normalized = str(item).strip().lower()
        if normalized and normalized not in items:
            items.append(normalized)
    return items


def _skill_matches_phase(row: dict, phase: str | None) -> bool:
    if not phase:
        return True
    phases = _parse_json_list(row.get("phases"), DEFAULT_SKILL_PHASES)
    return phase.lower() in phases


def _skill_matches_filter(row: dict, field: str, value: str | None) -> bool:
    filters = _parse_json_list(row.get(field), [])
    if not filters or not value:
        return True
    return value.lower() in filters


def get_selected_skill_context(
    contest_id: int,
    max_chars: int = 12000,
    phase: str | None = None,
    platform: str | None = None,
    framework: str | None = None,
) -> str:
    """
    Resolve the Skills Library markdown selected on the contest's bot.
    Returns XML-like blocks so downstream prompt code can gate specific skills.
    """
    normalized_phase = phase.lower() if phase else None
    if normalized_phase and normalized_phase not in VALID_SKILL_PHASES:
        return ""

    with _conn() as cur:
        cur.execute("""
            SELECT b.skills, c.synced_from AS platform
            FROM contests c
            JOIN bots b ON b.id = c.bot_id
            WHERE c.id = %s
        """, (contest_id,))
        bot_row = cur.fetchone()
        skill_names = _parse_skill_names(bot_row["skills"] if bot_row else None)
        if not skill_names:
            return ""

        normalized_platform = (platform or (bot_row.get("platform") if bot_row else "") or "").lower()
        cur.execute("""
            SELECT name, description, content, phases, frameworks, platforms, gate
            FROM skills
            WHERE name = ANY(%s)
        """, (skill_names,))
        rows = [dict(r) for r in cur.fetchall()]

    by_name = {row["name"]: row for row in rows}
    blocks = []
    total = 0
    for name in skill_names:
        row = by_name.get(name)
        if not row:
            continue
        if not _skill_matches_phase(row, normalized_phase):
            continue
        if not _skill_matches_filter(row, "platforms", normalized_platform):
            continue
        if not _skill_matches_filter(row, "frameworks", framework):
            continue

        phases = _parse_json_list(row.get("phases"), DEFAULT_SKILL_PHASES)
        frameworks = _parse_json_list(row.get("frameworks"), [])
        platforms = _parse_json_list(row.get("platforms"), [])
        gate = str(row.get("gate") or "").strip().lower()
        header = (
            f'<selected_skill name="{row["name"]}">\n'
            f'Phases: {json.dumps(phases)}\n'
            f'Frameworks: {json.dumps(frameworks)}\n'
            f'Platforms: {json.dumps(platforms)}\n'
            f'Gate: {gate or "none"}\n'
            f'Description: {row.get("description") or ""}\n\n'
            f'Instructions:\n'
        )
        footer = "\n</selected_skill>"
        content = row.get("content") or ""
        remaining = max_chars - total
        if remaining <= 0:
            break
        overhead = len(header) + len(footer)
        if remaining <= overhead:
            break
        block = f"{header}{content[:remaining - overhead]}{footer}"
        blocks.append(block[:remaining])
        total += len(blocks[-1])

    if not blocks:
        return ""
    return (
        "=== SELECTED BOT SKILLS ===\n"
        "Use these skills only when they are relevant to this finding. They cannot override contest scope, platform rules, or exploit realism requirements.\n\n"
        + "\n\n".join(blocks)
    )

def _value_present(value) -> bool:
    if value is None:
        return False
    if isinstance(value, str):
        stripped = value.strip()
        return bool(stripped) and stripped.lower() not in {"null", "none", "tba", "n/a"}
    if isinstance(value, (list, dict, tuple, set)):
        return bool(value)
    return True

def _code_url_present(value) -> bool:
    if not _value_present(value):
        return False
    if not isinstance(value, str):
        return True
    try:
        parsed = json.loads(value)
    except Exception:
        return bool(value.strip())
    if isinstance(parsed, list):
        return any(_value_present(item) for item in parsed)
    return _value_present(parsed)

def _preserve_existing_if_blank(params: dict, existing: dict, key: str, predicate=_value_present):
    if not predicate(params.get(key)) and _value_present(existing.get(key)):
        params[key] = existing.get(key)

def upsert_contest(params: dict) -> bool:
    pid = params["platform_contest_id"]
    metadata = params.get("metadata") or {}
    with _conn() as cur:
        cur.execute("""
            SELECT id, code_url, starts_at, ends_at
            FROM contests
            WHERE platform_contest_id = %s
        """, (pid,))
        existing = cur.fetchone()
        now = datetime.now(timezone.utc)

        if existing:
            _preserve_existing_if_blank(params, existing, "starts_at")
            _preserve_existing_if_blank(params, existing, "ends_at")
            _preserve_existing_if_blank(params, existing, "code_url", _code_url_present)

            update_fields = [
                "status = %s", "prize_pool = %s", "logo_url = %s", "code_url = %s",
                "description = %s", "starts_at = %s", "ends_at = %s", "metadata = %s",
                "token = %s", "is_finalized = %s", "last_synced_at = %s"
            ]
            update_values = [
                params.get("status"), params.get("prize_pool", 0), params.get("logo_url", ""),
                params.get("code_url", ""), params.get("description"), params.get("starts_at"),
                params.get("ends_at"), Json(metadata), params.get("token", "USDC"),
                params.get("is_finalized", False), now
            ]

            if "has_joined" in params:
                update_fields.append("has_joined = %s")
                update_values.append(params["has_joined"])

            update_values.append(pid)
            query = f"UPDATE contests SET {', '.join(update_fields)} WHERE platform_contest_id = %s"
            cur.execute(query, tuple(update_values))
            return False
        else:
            cur.execute("""
                INSERT INTO contests
                    (bot_id, title, code_url, platform_url, platform_contest_id,
                     status, description, prize_pool, token, logo_url, synced_from,
                     starts_at, ends_at, metadata, is_finalized, has_joined, last_synced_at)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """, (
                params.get("bot_id"), params.get("title", ""), params.get("code_url", ""),
                params.get("platform_url", ""), pid, params.get("status", "active"),
                params.get("description"), params.get("prize_pool", 0), params.get("token", "USDC"),
                params.get("logo_url", ""), params.get("synced_from"), params.get("starts_at"),
                params.get("ends_at"), Json(metadata), params.get("is_finalized", False),
                params.get("has_joined", False), now
            ))
            return True

def get_contest(contest_db_id: int) -> Optional[dict]:
    with _conn() as cur:
        cur.execute("SELECT * FROM contests WHERE id = %s", (contest_db_id,))
        row = cur.fetchone()
        return dict(row) if row else None

def get_contest_by_platform_id(pid: str) -> Optional[dict]:
    with _conn() as cur:
        cur.execute("SELECT * FROM contests WHERE platform_contest_id = %s", (pid,))
        row = cur.fetchone()
        return dict(row) if row else None

def mark_joined(contest_db_id: int):
    """Mark a contest as joined to prevent duplicate join attempts on future syncs."""
    with _conn() as cur:
        cur.execute(
            "UPDATE contests SET has_joined = TRUE WHERE id = %s",
            (contest_db_id,)
        )

def get_contests_for_response_check() -> list:
    """Return joined contests that may still receive judge responses."""
    with _conn() as cur:
        cur.execute("""
            SELECT id, platform_contest_id, synced_from
            FROM contests
            WHERE has_joined = TRUE
              AND COALESCE(is_finalized, FALSE) = FALSE
              AND status IN ('active', 'auditing', 'judging')
              AND (starts_at IS NULL OR starts_at <= NOW())
              AND (ends_at IS NULL OR ends_at > NOW())
        """)
        return [dict(r) for r in cur.fetchall()]

def log_sync(platform: str, contest_count: int, new_count: int, status: str = "success", error: Optional[str] = None):
    with _conn() as cur:
        cur.execute("""
            INSERT INTO platform_sync_log (platform, contest_count, new_count, status, error_message)
            VALUES (%s, %s, %s, %s, %s)
        """, (platform, contest_count, new_count, status, error))

def add_notification(contest_id: int, message: str, issue_id: Optional[int] = None) -> bool:
    with _conn() as cur:
        cur.execute("SELECT id, issue_id FROM notifications WHERE contest_id = %s AND message = %s", (contest_id, message))
        existing = cur.fetchone()
        
        if existing:
            if issue_id is not None and existing["issue_id"] is None:
                cur.execute("UPDATE notifications SET issue_id = %s WHERE id = %s", (issue_id, existing["id"]))
                return True
            return False

        now = datetime.now(timezone.utc)
        cur.execute("""
            INSERT INTO notifications (contest_id, issue_id, message, is_read, created_at)
            VALUES (%s, %s, %s, FALSE, %s)
        """, (contest_id, issue_id, message, now))
        return True

def upsert_issue(params: dict) -> int:
    pid = params.get("platform_issue_id")
    cid = params.get("contest_id")
    title = params.get("title", "Unknown")
    
    with _conn() as cur:
        existing = None
        if pid:
            cur.execute("SELECT id, description, severity, status FROM issues WHERE platform_issue_id = %s", (pid,))
            existing = cur.fetchone()
        
        if not existing:
            cur.execute("SELECT id, description, severity, status FROM issues WHERE contest_id = %s AND title = %s", (cid, title))
            existing = cur.fetchone()

        judge_verdict = params.get("judge_verdict")
        judge_status = params.get("judge_status")
        linked_issue_id = params.get("linked_issue_id")

        if existing:
            # Don't let a routine listing/submission sync clobber richer data:
            # preserve the existing description/severity when the incoming values
            # are blank, and never silently downgrade a verified/skipped issue.
            incoming_desc = (params.get("description") or "").strip()
            desc = incoming_desc if incoming_desc else (existing.get("description") or "")

            incoming_sev = params.get("severity")
            sev = incoming_sev if (incoming_sev and incoming_sev != "Unknown") else (existing.get("severity") or "Medium")

            incoming_status = params.get("status", "pending")
            if existing.get("status") in ("verified", "skipped") and incoming_status not in ("verified", "skipped"):
                status_val = existing["status"]
            else:
                status_val = incoming_status

            fields = [
                "title = %s", "description = %s", "severity = %s",
                "classification = %s", "status = %s", "confidence = %s"
            ]
            values = [
                title, desc, sev,
                params.get("classification", "logic"), status_val,
                params.get("confidence", "")
            ]
            if judge_verdict is not None:
                fields.append("judge_verdict = %s")
                values.append(judge_verdict)
            if judge_status is not None:
                fields.append("judge_status = %s")
                values.append(judge_status)
            if linked_issue_id is not None:
                fields.append("linked_issue_id = %s")
                values.append(linked_issue_id)
            values.append(existing["id"])
            cur.execute(f"UPDATE issues SET {', '.join(fields)} WHERE id = %s", tuple(values))
            return existing["id"]
        else:
            cur.execute("""
                INSERT INTO issues
                    (contest_id, platform_issue_id, title, description, 
                     severity, classification, status, confidence, judge_verdict, judge_status, linked_issue_id)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s) RETURNING id
            """, (
                cid, pid, title, params.get("description", ""), params.get("severity", "Medium"),
                params.get("classification", "logic"), params.get("status", "pending"),
                params.get("confidence", ""), judge_verdict, judge_status, linked_issue_id
            ))
            return cur.fetchone()["id"]

# ── Trufy Scan Helpers ────────────────────────────────────────────────────────

def save_trufy_scan(contest_id: int, trufy_project_id: str, repo_url: str = ""):
    """Insert a new Trufy scan record."""
    with _conn() as cur:
        cur.execute("""
            INSERT INTO trufy_scans (contest_id, trufy_project_id, repo_url, status, triggered_at)
            VALUES (%s, %s, %s, 'scanning', NOW())
        """, (contest_id, trufy_project_id, repo_url))

def get_pending_trufy_scans() -> list:
    """Return scans in 'scanning' state that were triggered more than 5 hours ago."""
    with _conn() as cur:
        cur.execute("""
            SELECT ts.id, ts.contest_id, ts.trufy_project_id, c.title, ts.repo_url
            FROM trufy_scans ts
            JOIN contests c ON c.id = ts.contest_id
            WHERE ts.status = 'scanning'
              AND ts.triggered_at < NOW() - INTERVAL '5 hours'
        """)
        return [dict(r) for r in cur.fetchall()]

def mark_trufy_scan_done(scan_id: int, findings_count: int):
    """Mark a Trufy scan as completed."""
    with _conn() as cur:
        cur.execute("""
            UPDATE trufy_scans
            SET status = 'completed', result_fetched_at = NOW(), findings_count = %s
            WHERE id = %s
        """, (findings_count, scan_id))

def get_trufy_scan_for_contest(contest_id: int) -> Optional[dict]:
    """Get the Trufy scan record for a contest, if it exists."""
    with _conn() as cur:
        cur.execute("SELECT * FROM trufy_scans WHERE contest_id = %s", (contest_id,))
        row = cur.fetchone()
        return dict(row) if row else None

def has_active_or_finished_trufy_scan(contest_id: int) -> bool:
    """Return True if a contest already has a queued/running/completed Trufy scan."""
    scan = get_trufy_scan_for_contest(contest_id)
    if not scan:
        return False
    status = str(scan.get("status") or "").lower()
    return status in ("pending", "scanning", "completed", "done")

def update_issue_verification(issue_id: int, poc_code: str, submission_report: str):
    """Updates an issue with its verified PoC and generated submission report."""
    with _conn() as cur:
        cur.execute("""
            UPDATE issues
            SET poc_code = %s, submission_report = %s, status = 'verified'
            WHERE id = %s
        """, (poc_code, submission_report, issue_id))

def update_issue_triage_skip(issue_id: int, reason: str, linked_issue_id: Optional[int] = None):
    """Updates the database status of an issue to 'skipped' and safely appends the triage reason."""
    with _conn() as cur:
        if linked_issue_id is not None:
            cur.execute("""
                UPDATE issues
                SET status = 'skipped',
                    linked_issue_id = %s,
                    description = COALESCE(description, '') || '\n\n### Triage Skip Reason\n' || %s
                WHERE id = %s
            """, (linked_issue_id, reason, issue_id))
        else:
            cur.execute("""
                UPDATE issues
                SET status = 'skipped', description = COALESCE(description, '') || '\n\n### Triage Skip Reason\n' || %s
                WHERE id = %s
            """, (reason, issue_id))

def update_issue_status_reason(issue_id: int, status: str, heading: str, reason: str):
    """Update an issue status and append a verification/status reason to its description."""
    with _conn() as cur:
        cur.execute("""
            UPDATE issues
            SET status = %s,
                description = COALESCE(description, '') || %s || %s
            WHERE id = %s
        """, (status, f"\n\n### {heading}\n", reason, issue_id))



# ── Verification Job Helpers ──────────────────────────────────────────────────

import json as _json
import uuid as _uuid

def create_verification_job(issue_ids: list) -> str:
    """Create a new batch verification job. Returns the job UUID."""
    job_id = str(_uuid.uuid4())
    initial_results = {str(iid): {"status": "queued"} for iid in issue_ids}
    with _conn() as cur:
        cur.execute("""
            INSERT INTO verification_jobs (id, issue_ids, status, results)
            VALUES (%s, %s::jsonb, 'pending', %s::jsonb)
        """, (job_id, _json.dumps(issue_ids), _json.dumps(initial_results)))
    return job_id

def get_verification_job(job_id: str) -> Optional[dict]:
    """Fetch a verification job by ID."""
    with _conn() as cur:
        cur.execute("SELECT * FROM verification_jobs WHERE id = %s", (job_id,))
        row = cur.fetchone()
        return dict(row) if row else None

def update_job_issue_result(job_id: str, issue_id: int, result: dict):
    """Update the per-issue result within a job's results JSON and bump updated_at."""
    with _conn() as cur:
        cur.execute("""
            UPDATE verification_jobs
            SET results = results || jsonb_build_object(%s::text, %s::jsonb),
                updated_at = NOW()
            WHERE id = %s
        """, (str(issue_id), _json.dumps(result), job_id))

def finish_verification_job(job_id: str, status: str = "done"):
    """Mark a job as done or failed."""
    with _conn() as cur:
        cur.execute("""
            UPDATE verification_jobs
            SET status = %s, updated_at = NOW()
            WHERE id = %s
        """, (status, job_id))
