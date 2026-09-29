from __future__ import annotations
from fastapi import FastAPI, Depends, APIRouter, File, UploadFile, Form, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from typing import Optional, List, Dict, Any
import json
import os
import shutil
import time
from datetime import datetime, timezone

from .db import get_db, redis_client

app = FastAPI(title="AuditBot API")

# Configure CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Root directory for static file uploads
# UPLOAD_DIR = "/app/.codex/uploads"
UPLOAD_DIR = os.getenv("UPLOAD_DIR", "/app/.codex/uploads")
os.makedirs(UPLOAD_DIR, exist_ok=True)

# -----------------------------------------------------------------------------
# Pydantic Schemas
# -----------------------------------------------------------------------------

class BotCreate(BaseModel):
    name: str
    platform: str
    mode: Optional[str] = "review"
    skills: Optional[Any] = None
    status: Optional[str] = "idle"

class BotUpdate(BaseModel):
    mode: Optional[str] = None
    status: Optional[str] = None
    skills: Optional[Any] = None

class SkillCreate(BaseModel):
    name: str
    description: str
    content: str
    phases: Optional[Any] = None
    frameworks: Optional[Any] = None
    platforms: Optional[Any] = None
    gate: Optional[str] = None

class SkillUpdate(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    content: Optional[str] = None
    phases: Optional[Any] = None
    frameworks: Optional[Any] = None
    platforms: Optional[Any] = None
    gate: Optional[str] = None

class IssueCreate(BaseModel):
    contest_id: int
    title: str

class IssueUpdate(BaseModel):
    title: Optional[str] = None
    description: Optional[str] = None
    severity: Optional[str] = None
    classification: Optional[str] = None
    status: Optional[str] = None

class BatchVerifyRequest(BaseModel):
    issue_ids: List[int]

# -----------------------------------------------------------------------------
# Routers
# -----------------------------------------------------------------------------

bots_router = APIRouter(tags=["Bots"])
contests_router = APIRouter(tags=["Contests"])
issues_router = APIRouter(tags=["Issues"])
skills_router = APIRouter(tags=["Skills"])
platform_router = APIRouter(tags=["Platform"])
notifications_router = APIRouter(tags=["Notifications"])
jobs_router = APIRouter(tags=["Jobs"])

VALID_SKILL_PHASES = ("triage", "poc", "report")
DEFAULT_SKILL_PHASES = list(VALID_SKILL_PHASES)
VALID_SKILL_GATES = {"foundry_mainnet_fork"}


def _normalize_skill_names(value: Any) -> List[str]:
    if value is None:
        return []
    if isinstance(value, list):
        raw = value
    elif isinstance(value, str):
        stripped = value.strip()
        if not stripped:
            return []
        try:
            parsed = json.loads(stripped)
            raw = parsed if isinstance(parsed, list) else [parsed]
        except json.JSONDecodeError:
            raw = [stripped]
    else:
        raw = [value]

    names: List[str] = []
    for item in raw:
        name = str(item).strip()
        if name and name not in names:
            names.append(name)
    return names


def _serialize_skill_names(value: Any) -> str:
    return json.dumps(_normalize_skill_names(value))


def _normalize_bot_row(row: dict) -> dict:
    data = dict(row)
    data["skills"] = _normalize_skill_names(data.get("skills"))
    return data


def _normalize_string_list(
    value: Any,
    *,
    default: Optional[List[str]] = None,
    allowed: Optional[set[str]] = None,
) -> List[str]:
    if value is None:
        return list(default or [])
    if isinstance(value, list):
        raw = value
    elif isinstance(value, str):
        stripped = value.strip()
        if not stripped:
            return []
        try:
            parsed = json.loads(stripped)
            raw = parsed if isinstance(parsed, list) else [parsed]
        except json.JSONDecodeError:
            raw = [part.strip() for part in stripped.split(",")]
    else:
        raw = [value]

    items: List[str] = []
    for item in raw:
        normalized = str(item).strip().lower()
        if not normalized:
            continue
        if allowed and normalized not in allowed:
            continue
        if normalized not in items:
            items.append(normalized)
    return items


def _normalize_skill_gate(value: Any) -> Optional[str]:
    if value is None:
        return None
    normalized = str(value).strip().lower()
    if normalized in {"", "none", "null"}:
        return None
    if normalized not in VALID_SKILL_GATES:
        raise ValueError(f"Unsupported skill gate: {normalized}")
    return normalized


def _normalize_skill_row(row: dict) -> dict:
    data = dict(row)
    data["phases"] = _normalize_string_list(
        data.get("phases"),
        default=DEFAULT_SKILL_PHASES,
        allowed=set(VALID_SKILL_PHASES),
    )
    data["frameworks"] = _normalize_string_list(data.get("frameworks"), default=[])
    data["platforms"] = _normalize_string_list(data.get("platforms"), default=[])
    data["gate"] = _normalize_skill_gate(data.get("gate"))
    return data


def _skill_metadata_payload(skill: SkillCreate | SkillUpdate) -> dict:
    payload = {}
    if isinstance(skill, SkillCreate) or skill.phases is not None:
        payload["phases"] = _normalize_string_list(
            skill.phases,
            default=DEFAULT_SKILL_PHASES,
            allowed=set(VALID_SKILL_PHASES),
        )
    if isinstance(skill, SkillCreate) or skill.frameworks is not None:
        payload["frameworks"] = _normalize_string_list(skill.frameworks, default=[])
    if isinstance(skill, SkillCreate) or skill.platforms is not None:
        payload["platforms"] = _normalize_string_list(skill.platforms, default=[])
    if isinstance(skill, SkillCreate) or skill.gate is not None:
        payload["gate"] = _normalize_skill_gate(skill.gate)
    return payload


# --- BOTS ---
@bots_router.get("/api/bots")
def list_bots(db=Depends(get_db)):
    with db.cursor() as cur:
        cur.execute("SELECT * FROM bots ORDER BY id DESC")
        return [_normalize_bot_row(row) for row in cur.fetchall()]

@bots_router.post("/api/bots")
def create_bot(bot: BotCreate, db=Depends(get_db)):
    with db.cursor() as cur:
        cur.execute(
            """
            INSERT INTO bots (name, platform, mode, skills, status)
            VALUES (%s, %s, %s, %s, %s)
            RETURNING id
            """,
            (
                bot.name,
                bot.platform.lower(),
                bot.mode or "review",
                _serialize_skill_names(bot.skills),
                bot.status or "idle",
            )
        )
        db.commit()
        return {"id": cur.fetchone()["id"]}

@bots_router.patch("/api/bots/{bot_id}")
def update_bot(bot_id: int, bot: BotUpdate, db=Depends(get_db)):
    with db.cursor() as cur:
        fields, values = [], []
        if bot.mode is not None:
            fields.append("mode = %s")
            values.append(bot.mode)
        if bot.status is not None:
            fields.append("status = %s")
            values.append(bot.status)
        if bot.skills is not None:
            fields.append("skills = %s")
            values.append(_serialize_skill_names(bot.skills))
            
        if fields:
            values.append(bot_id)
            cur.execute(f"UPDATE bots SET {', '.join(fields)} WHERE id = %s", values)
            db.commit()
        return {"success": True}

@bots_router.delete("/api/bots/{bot_id}")
def delete_bot(bot_id: int, db=Depends(get_db)):
    with db.cursor() as cur:
        cur.execute("DELETE FROM bots WHERE id = %s", (bot_id,))
        db.commit()
    return {"success": True}


# --- CONTESTS ---
@contests_router.get("/api/contests")
def list_contests(db=Depends(get_db)):
    with db.cursor() as cur:
        cur.execute("""
            SELECT c.*, b.name AS bot_name
            FROM contests c
            LEFT JOIN bots b ON c.bot_id = b.id
            ORDER BY c.starts_at DESC NULLS LAST, c.id DESC
        """)
        rows = cur.fetchall()
        for r in rows:
            code_url = r.get("code_url")
            parsed_urls = []
            if code_url:
                try:
                    parsed_urls = json.loads(code_url)
                    if not isinstance(parsed_urls, list):
                        parsed_urls = [code_url]
                except Exception:
                    parsed_urls = [code_url]
            r["code_urls"] = parsed_urls
        return rows

@contests_router.get("/api/contests/{contest_id}")
def get_contest_detail(contest_id: int, db=Depends(get_db)):
    with db.cursor() as cur:
        cur.execute("""
            SELECT c.*, b.name AS bot_name
            FROM contests c
            LEFT JOIN bots b ON c.bot_id = b.id
            WHERE c.id = %s
        """, (contest_id,))
        r = cur.fetchone()
        if not r:
            raise HTTPException(status_code=404, detail="Contest not found")
        
        # Parse code_url
        code_url = r.get("code_url")
        parsed_urls = []
        if code_url:
            try:
                parsed_urls = json.loads(code_url)
                if not isinstance(parsed_urls, list):
                    parsed_urls = [code_url]
            except Exception:
                parsed_urls = [code_url]
        r["code_urls"] = parsed_urls
        return r

@contests_router.post("/api/contests/discover")
def discover_contests():
    # Enqueue a sync job
    redis_client.lpush("jobs", json.dumps({"action": "sync_all"}))
    return {"success": True}

@contests_router.post("/api/contests")
async def create_contest(
    bot_id: int = Form(...),
    title: str = Form(...),
    platform_url: str = Form(""),
    file: UploadFile = File(None),
    db=Depends(get_db)
):
    code_url = ""
    if file:
        file_ext = os.path.splitext(file.filename)[1]
        filename = f"file-{int(time.time() * 1000)}{file_ext}"
        filepath = os.path.join(UPLOAD_DIR, filename)
        with open(filepath, "wb") as buffer:
            shutil.copyfileobj(file.file, buffer)
        code_url = f"/uploads/{filename}"

    platform_contest_id = f"manual-{int(time.time() * 1000)}"

    with db.cursor() as cur:
        cur.execute("""
            INSERT INTO contests
                (bot_id, title, code_url, platform_url, platform_contest_id, synced_from)
            VALUES (%s, %s, %s, %s, %s, 'manual')
            RETURNING id
        """, (bot_id, title, code_url, platform_url, platform_contest_id))
        db.commit()
        return {"id": cur.fetchone()["id"]}

@contests_router.post("/api/contests/{contest_id}/ping")
def ping_contest(contest_id: int, db=Depends(get_db)):
    with db.cursor() as cur:
        cur.execute("SELECT synced_from FROM contests WHERE id = %s", (contest_id,))
        contest = cur.fetchone()
        
    if contest and contest.get("synced_from") != "manual":
        # Enqueue job
        redis_client.lpush("jobs", json.dumps({
            "action": "ping_contest",
            "contest_id": contest_id,
            "platform": contest["synced_from"],
            "force": True
        }))
    return {"success": True}

@contests_router.delete("/api/contests/{contest_id}")
def delete_contest(contest_id: int, db=Depends(get_db)):
    with db.cursor() as cur:
        cur.execute("SELECT id FROM contests WHERE id = %s", (contest_id,))
        if not cur.fetchone():
            raise HTTPException(status_code=404, detail="Contest not found")

        cur.execute("DELETE FROM notifications WHERE contest_id = %s", (contest_id,))
        cur.execute("DELETE FROM trufy_scans WHERE contest_id = %s", (contest_id,))
        cur.execute("DELETE FROM issues WHERE contest_id = %s", (contest_id,))
        cur.execute("DELETE FROM contests WHERE id = %s", (contest_id,))
        db.commit()

    return {"success": True}

@contests_router.post("/api/contests/{contest_id}/join")
def join_contest(contest_id: int, db=Depends(get_db)):
    with db.cursor() as cur:
        cur.execute("SELECT synced_from, has_joined, starts_at, ends_at FROM contests WHERE id = %s", (contest_id,))
        contest = cur.fetchone()

    if not contest:
        return {"success": False, "message": "Contest not found."}

    now = datetime.now(timezone.utc)
    starts_at = contest.get("starts_at")
    ends_at = contest.get("ends_at")
    if starts_at:
        start_dt = starts_at if starts_at.tzinfo else starts_at.replace(tzinfo=timezone.utc)
        if start_dt > now:
            return {"success": False, "message": "Contest has not started yet."}
    if ends_at:
        end_dt = ends_at if ends_at.tzinfo else ends_at.replace(tzinfo=timezone.utc)
        if end_dt <= now:
            return {"success": False, "message": "Contest has already ended."}

    if contest and not contest.get("has_joined") and contest.get("synced_from") != "manual":
        # Enqueue join job
        redis_client.lpush("jobs", json.dumps({
            "action": "join_contest",
            "contest_id": contest_id,
            "platform": contest["synced_from"]
        }))
        return {"success": True, "message": "Join request queued."}
    
    return {"success": False, "message": "Already joined or manual contest."}

class SkipAutojoinUpdate(BaseModel):
    skip: bool

@contests_router.patch("/api/contests/{contest_id}/skip-autojoin")
def toggle_skip_autojoin(contest_id: int, body: SkipAutojoinUpdate, db=Depends(get_db)):
    """Toggle the skip_autojoin flag. When True, bot will never auto-join this contest."""
    with db.cursor() as cur:
        cur.execute(
            "UPDATE contests SET skip_autojoin = %s, has_joined = %s WHERE id = %s",
            (body.skip, not body.skip, contest_id)
        )
        db.commit()
    return {"success": True, "skip_autojoin": body.skip}


@contests_router.post("/api/contests/{contest_id}/trufy-scan")
def trigger_trufy_scan(contest_id: int, db=Depends(get_db)):
    """Manually trigger a Trufy AI scan for a contest."""
    with db.cursor() as cur:
        cur.execute("SELECT id, title, code_url FROM contests WHERE id = %s", (contest_id,))
        contest = cur.fetchone()
    if not contest:
        raise HTTPException(status_code=404, detail="Contest not found")
    redis_client.lpush("jobs", json.dumps({
        "action": "trufy_scan",
        "contest_id": contest_id
    }))
    return {"success": True, "message": f"Trufy scan queued for '{contest['title']}'."}

@contests_router.get("/api/contests/{contest_id}/trufy-scan")
def get_trufy_scan_status(contest_id: int, db=Depends(get_db)):
    """Get the Trufy AI scan status and findings count for a contest."""
    with db.cursor() as cur:
        cur.execute("""
            SELECT status, repo_url 
            FROM trufy_scans 
            WHERE contest_id = %s
        """, (contest_id,))
        scans = cur.fetchall()
        
    if not scans:
        return {"exists": False, "scan_status": "not_started", "total": 0, "completed": 0}
        
    total_scans = len(scans)
    completed_scans = sum(1 for s in scans if s["status"] in ["completed", "failed"])
    
    scan_status = "completed" if completed_scans == total_scans else "pending"
    
    return {
        "exists": True, 
        "scan_status": scan_status,
        "total": total_scans,
        "completed": completed_scans
    }

# --- ISSUES ---
@issues_router.get("/api/issues/{contest_id}")
def list_issues(contest_id: int, db=Depends(get_db)):
    with db.cursor() as cur:
        cur.execute("SELECT * FROM issues WHERE contest_id = %s ORDER BY id DESC", (contest_id,))
        return cur.fetchall()

@issues_router.post("/api/issues")
def create_issue(issue: IssueCreate, db=Depends(get_db)):
    with db.cursor() as cur:
        platform_issue_id = f"manual-{int(time.time() * 1000)}"
        cur.execute("""
            INSERT INTO issues (contest_id, platform_issue_id, title, status)
            VALUES (%s, %s, %s, 'pending') RETURNING id
        """, (issue.contest_id, platform_issue_id, issue.title))
        db.commit()
        return {"id": cur.fetchone()["id"]}

@issues_router.patch("/api/issues/{issue_id}")
def update_issue(issue_id: int, update: IssueUpdate, db=Depends(get_db)):
    with db.cursor() as cur:
        fields, values = [], []
        if update.title is not None:
             fields.append("title = %s")
             values.append(update.title)
        if update.description is not None:
             fields.append("description = %s")
             values.append(update.description)
        if update.severity is not None:
             fields.append("severity = %s")
             values.append(update.severity)
        if update.classification is not None:
             fields.append("classification = %s")
             values.append(update.classification)
        if update.status is not None:
             fields.append("status = %s")
             values.append(update.status)

        if fields:
            values.append(issue_id)
            cur.execute(f"UPDATE issues SET {', '.join(fields)} WHERE id = %s", values)
            db.commit()
        return {"success": True}

@issues_router.post("/api/issues/{issue_id}/submit-to-platform")
def submit_to_platform(issue_id: int, db=Depends(get_db)):
    with db.cursor() as cur:
        # Get original issue
        cur.execute("SELECT * FROM issues WHERE id = %s", (issue_id,))
        issue = cur.fetchone()
        if not issue:
            raise HTTPException(status_code=404, detail="Issue not found")
            
        cur.execute("SELECT synced_from AS platform FROM contests WHERE id = %s", (issue['contest_id'],))
        contest = cur.fetchone()
        if not contest:
            raise HTTPException(status_code=404, detail="Contest not found")
            
        platform = contest['platform']
        cur.execute("""
            SELECT id, platform_issue_id
            FROM issues
            WHERE linked_issue_id = %s
              AND classification = 'platform-submission'
            ORDER BY id DESC
            LIMIT 1
        """, (issue_id,))
        existing_submission = cur.fetchone()
        if existing_submission:
            cur.execute("UPDATE issues SET status = 'submitted' WHERE id = %s", (issue_id,))
            db.commit()
            return {
                "success": True,
                "new_issue_id": existing_submission["id"],
                "platform_issue_id": existing_submission["platform_issue_id"],
                "already_submitted": True,
            }

        if platform == 'code4rena' and not issue.get('submission_report'):
            raise HTTPException(status_code=400, detail="Issue has no generated submission report")
        if platform == 'code4rena' and not issue.get('poc_code'):
            raise HTTPException(status_code=400, detail="Issue has no verified PoC")
        
        platform_issue_id = None
        if platform == 'code4rena':
            from worker.code4rena.bot import submit_finding_to_platform
            platform_issue_id = submit_finding_to_platform(issue)
            if not platform_issue_id:
                raise HTTPException(status_code=502, detail="Code4rena submission failed")
        elif platform == 'sherlock':
            from worker.sherlock.bot import submit_finding_to_platform
            platform_issue_id = submit_finding_to_platform(issue)
            if not platform_issue_id:
                raise HTTPException(status_code=502, detail="Sherlock submission failed — check GITHUB_TOKEN and repo access")
        elif platform == 'guardian':
            from worker.guardian.bot import submit_finding_to_platform
            platform_issue_id = submit_finding_to_platform(issue)
            if not platform_issue_id:
                raise HTTPException(status_code=502, detail="Guardian submission failed — check credentials")
        else:
            # Unsupported platform — generate mock ID
            import uuid
            platform_issue_id = f"{platform}-mock-{uuid.uuid4().hex[:8]}"
            
        submission_description = issue.get('submission_report') or issue['description']

        # Update original issue status to submitted
        cur.execute("UPDATE issues SET status = 'submitted' WHERE id = %s", (issue_id,))
        
        # Create new platform submission row linked to original issue
        cur.execute("""
            INSERT INTO issues (contest_id, platform_issue_id, title, description, severity, classification, status, linked_issue_id)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
            RETURNING id
        """, (
            issue['contest_id'],
            platform_issue_id,
            issue['title'],
            submission_description,
            issue['severity'],
            "platform-submission",
            "pending",
            issue_id
        ))
        new_id = cur.fetchone()['id']
        db.commit()
        
        return {"success": True, "new_issue_id": new_id, "platform_issue_id": platform_issue_id}

@issues_router.post("/api/issues/{issue_id}/verify")
def verify_issue(issue_id: int):
    # Enqueue verify job
    redis_client.lpush("jobs", json.dumps({"action": "verify_issue", "issue_id": issue_id}))
    return {"success": True, "message": "Verification job queued."}


@issues_router.post("/api/issues/batch-verify")
def batch_verify_issues(body: BatchVerifyRequest):
    """
    Queue a batch verification job for multiple issues.
    Returns a job_id immediately. Poll GET /api/jobs/{job_id} for live progress.
    """
    if not body.issue_ids:
        raise HTTPException(status_code=400, detail="issue_ids must not be empty")

    # Create the job record in Postgres
    import sys, os as _os
    sys.path.insert(0, "/app")
    from worker.shared.db import create_verification_job
    job_id = create_verification_job(body.issue_ids)

    # Push to Redis so the worker picks it up
    redis_client.lpush("jobs", json.dumps({
        "action": "batch_verify",
        "job_id": job_id,
        "issue_ids": body.issue_ids
    }))
    return {"success": True, "job_id": job_id}


# --- JOBS ---
@jobs_router.get("/api/jobs/{job_id}")
def get_job_status(job_id: str):
    """
    Poll this endpoint to get live progress of a batch verification job.
    Returns:
      status: pending | running | done | failed
      results: dict keyed by issue_id, each with:
        status: queued | verifying | verified | invalid | unsupported | unverified | skipped | failed
        poc_code (if verified)
        reason / error / framework / logs (if skipped/failed/invalid/unsupported)
    """
    from worker.shared.db import get_verification_job
    job = get_verification_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    return job


# --- SKILLS ---
def sync_skill_to_file(
    name,
    description,
    content,
    phases=None,
    frameworks=None,
    platforms=None,
    gate=None,
):
    """Write skill to the .codex volume for external LLM parsing"""
    default_codex_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".codex"))
    codex_dir = os.environ.get("CODEX_DATA_DIR", default_codex_dir)
    base_dir = os.path.join(codex_dir, "skills")
    os.makedirs(base_dir, exist_ok=True)
    skill_dir = os.path.join(base_dir, name)
    os.makedirs(skill_dir, exist_ok=True)
    yaml_header = "\n".join([
        "---",
        f"name: {json.dumps(name)}",
        f"description: {json.dumps(description)}",
        f"phases: {json.dumps(_normalize_string_list(phases, default=DEFAULT_SKILL_PHASES, allowed=set(VALID_SKILL_PHASES)))}",
        f"frameworks: {json.dumps(_normalize_string_list(frameworks, default=[]))}",
        f"platforms: {json.dumps(_normalize_string_list(platforms, default=[]))}",
        f"gate: {json.dumps(_normalize_skill_gate(gate))}",
        "---",
        "",
        "",
    ])
    with open(os.path.join(skill_dir, "SKILL.md"), "w") as f:
        f.write(yaml_header + content)

@skills_router.get("/api/skills")
def list_skills(db=Depends(get_db)):
    with db.cursor() as cur:
        cur.execute("SELECT * FROM skills ORDER BY name ASC")
        return [_normalize_skill_row(row) for row in cur.fetchall()]

@skills_router.post("/api/skills")
def create_skill(skill: SkillCreate, db=Depends(get_db)):
    with db.cursor() as cur:
        try:
            metadata = _skill_metadata_payload(skill)
            cur.execute("""
                INSERT INTO skills (name, description, content, phases, frameworks, platforms, gate)
                VALUES (%s, %s, %s, %s::jsonb, %s::jsonb, %s::jsonb, %s)
                RETURNING id
            """, (
                skill.name,
                skill.description,
                skill.content,
                json.dumps(metadata["phases"]),
                json.dumps(metadata["frameworks"]),
                json.dumps(metadata["platforms"]),
                metadata["gate"],
            ))
            skill_id = cur.fetchone()["id"]
            db.commit()
            sync_skill_to_file(
                skill.name,
                skill.description,
                skill.content,
                metadata["phases"],
                metadata["frameworks"],
                metadata["platforms"],
                metadata["gate"],
            )
            return {"id": skill_id}
        except ValueError as e:
            db.rollback()
            raise HTTPException(status_code=400, detail=str(e))
        except Exception as e:
            db.rollback()
            raise HTTPException(status_code=400, detail=str(e))

@skills_router.patch("/api/skills/{skill_id}")
def update_skill(skill_id: int, update: SkillUpdate, db=Depends(get_db)):
    with db.cursor() as cur:
        try:
            fields, values = [], []
            if update.name is not None:
                 fields.append("name = %s")
                 values.append(update.name)
            if update.description is not None:
                 fields.append("description = %s")
                 values.append(update.description)
            if update.content is not None:
                 fields.append("content = %s")
                 values.append(update.content)

            metadata = _skill_metadata_payload(update)
            if "phases" in metadata:
                fields.append("phases = %s::jsonb")
                values.append(json.dumps(metadata["phases"]))
            if "frameworks" in metadata:
                fields.append("frameworks = %s::jsonb")
                values.append(json.dumps(metadata["frameworks"]))
            if "platforms" in metadata:
                fields.append("platforms = %s::jsonb")
                values.append(json.dumps(metadata["platforms"]))
            if "gate" in metadata:
                fields.append("gate = %s")
                values.append(metadata["gate"])

            if fields:
                values.append(skill_id)
                cur.execute(f"UPDATE skills SET {', '.join(fields)} WHERE id = %s RETURNING *", values)
                row = cur.fetchone()
                db.commit()
                if row:
                    normalized = _normalize_skill_row(row)
                    sync_skill_to_file(
                        normalized["name"],
                        normalized["description"],
                        normalized["content"],
                        normalized["phases"],
                        normalized["frameworks"],
                        normalized["platforms"],
                        normalized["gate"],
                    )
            return {"success": True}
        except ValueError as e:
            db.rollback()
            raise HTTPException(status_code=400, detail=str(e))
        except Exception as e:
            db.rollback()
            raise HTTPException(status_code=400, detail=str(e))

@skills_router.delete("/api/skills/{skill_id}")
def delete_skill(skill_id: int, db=Depends(get_db)):
    with db.cursor() as cur:
        cur.execute("DELETE FROM skills WHERE id = %s", (skill_id,))
        db.commit()
    return {"success": True}


# --- PLATFORM ---
@platform_router.get("/api/platform/status")
def platform_status():
    """Since worker runs in background, we'll fetch its status via redis"""
    st_s = redis_client.get("status_sherlock")
    st_g = redis_client.get("status_guardian")
    st_c = redis_client.get("status_code4rena")
    
    return {
        "sherlock": json.loads(st_s) if st_s else {"platform": "sherlock", "connected": False},
        "guardian": json.loads(st_g) if st_g else {"platform": "guardian", "connected": False},
        "code4rena": json.loads(st_c) if st_c else {"platform": "code4rena", "connected": False}
    }

@platform_router.post("/api/platform/sync")
def trigger_sync():
    redis_client.lpush("jobs", json.dumps({"action": "sync_all"}))
    return {"success": True, "message": "Sync queued."}

class SherlockCookieUpdate(BaseModel):
    cookie: str

@platform_router.post("/api/platform/sherlock/cookie")
def update_sherlock_cookie(body: SherlockCookieUpdate):
    """Update the Sherlock session cookie at runtime via Redis (no container restart needed)."""
    if not body.cookie or not body.cookie.strip():
        raise HTTPException(status_code=400, detail="Cookie string cannot be empty")
    redis_client.set("config:sherlock_cookie", body.cookie.strip())
    # Immediately trigger a re-sync so the new cookie is validated
    redis_client.lpush("jobs", json.dumps({"action": "sync_all"}))
    return {"success": True, "message": "Cookie updated. Re-sync queued."}

@platform_router.get("/api/platform/sync-log")
def sync_log(db=Depends(get_db)):
    with db.cursor() as cur:
        cur.execute("SELECT * FROM platform_sync_log ORDER BY synced_at DESC LIMIT 20")
        return cur.fetchall()


# --- NOTIFICATIONS ---
@notifications_router.get("/api/notifications")
def list_notifications(contestId: Optional[int] = None, db=Depends(get_db)):
    with db.cursor() as cur:
        if contestId:
            cur.execute("""
                SELECT n.*, c.title AS contest_title 
                FROM notifications n
                JOIN contests c ON n.contest_id = c.id
                WHERE n.contest_id = %s
                ORDER BY n.created_at DESC
            """, (contestId,))
        else:
            cur.execute("""
                SELECT n.*, c.title AS contest_title 
                FROM notifications n
                JOIN contests c ON n.contest_id = c.id
                WHERE n.is_read = FALSE 
                ORDER BY n.created_at DESC
            """)
        return cur.fetchall()

@notifications_router.get("/api/notifications/unread")
def get_unread_count(db=Depends(get_db)):
    try:
        with db.cursor() as cur:
            cur.execute("SELECT COUNT(*) as count FROM notifications WHERE is_read = FALSE")
            result = cur.fetchone()
            return {"count": result["count"] if result and "count" in result else 0}
    except Exception as e:
        print(f"Error fetching unread count: {e}")
        return {"count": 0}

@notifications_router.patch("/{notif_id}/read")
def read_notification(notif_id: int, db=Depends(get_db)):
    with db.cursor() as cur:
        cur.execute("UPDATE notifications SET is_read = TRUE WHERE id = %s", (notif_id,))
        db.commit()
    return {"success": True}

@notifications_router.patch("/read-all")
def read_all_notifications(db=Depends(get_db)):
    with db.cursor() as cur:
        cur.execute("UPDATE notifications SET is_read = TRUE WHERE is_read = FALSE")
        db.commit()
    return {"success": True}


# Register Routers
app.include_router(bots_router)
app.include_router(contests_router)
app.include_router(issues_router)
app.include_router(skills_router)
app.include_router(platform_router)
app.include_router(notifications_router)
app.include_router(jobs_router)

@app.get("/health")
def healthcheck():
    return {"status": "ok"}
