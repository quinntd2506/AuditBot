"""
Trufy Scanner Orchestration
---------------------------
High-level logic for: downloading contest repos, zipping them,
uploading to Trufy, triggering scans, and fetching results.
"""
import sys
import os
import re
import shutil
import subprocess
import tempfile
import argparse
from pathlib import Path
from typing import Optional

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from shared.config import GITHUB_TOKEN, REPOS_DIR
from shared.db import (
    get_contest, save_trufy_scan, get_pending_trufy_scans,
    mark_trufy_scan_done, upsert_issue, add_notification, get_trufy_scan_for_contest,
    update_issue_verification, update_issue_triage_skip, update_issue_status_reason,
    create_verification_job, get_verification_job, update_job_issue_result, finish_verification_job,
    get_selected_skill_context
)

try:
    from worker.verifier.agent import detect_framework, verify_issue_with_agent, triage_issue, MAX_POC_PER_CONTEST
    from worker.verifier.formatter import generate_submission_report
except ImportError:
    from verifier.agent import detect_framework, verify_issue_with_agent, triage_issue, MAX_POC_PER_CONTEST
    from verifier.formatter import generate_submission_report

# Import client — works whether run as script or as part of worker package
try:
    from worker.trufy import client
except ImportError:
    from trufy import client

# ── Repo Download & Zip ───────────────────────────────────────────────────────

def _extract_all_github_info(code_url_json: str) -> list[tuple[str, str]]:
    """
    Parse code_url (JSON array string) and extract all (repo_url, branch) pairs.
    """
    import json
    try:
        urls = json.loads(code_url_json) if code_url_json else []
    except Exception:
        urls = [code_url_json] if code_url_json else []

    results = []
    for url in urls:
        if url and "github.com" in url:
            # Normalize: strip .git suffix
            url = url.rstrip("/").removesuffix(".git")
            results.append((url, "main"))  # default branch; Trufy accepts any name for uploaded zips
    return results

def _extract_github_info(code_url_json: str) -> Optional[tuple[str, str]]:
    """Return the first GitHub repo/branch pair from a contest code_url field."""
    infos = _extract_all_github_info(code_url_json)
    return infos[0] if infos else None

def download_and_zip_repo(repo_url: str, branch: str = "main") -> Optional[Path]:
    """
    Download a GitHub archive zip, extract it to remove the top-level directory,
    and re-zip it flat. Trufy scanner expects a flat structure.
    Returns path to the .zip file (caller must clean up).
    """
    tmp_dir = Path(tempfile.mkdtemp(prefix="trufy_repo_"))
    archive_path = tmp_dir / "archive.zip"
    zip_path = tmp_dir / "repo.zip"

    print(f"[Trufy/Scanner] Downloading repo archive: {repo_url}")

    try:
        import requests
        import zipfile
        import shutil
        
        # Download archive
        archive_url = f"{repo_url}/archive/refs/heads/{branch}.zip"
        headers = {}
        if GITHUB_TOKEN:
            headers["Authorization"] = f"token {GITHUB_TOKEN}"
        resp = requests.get(archive_url, headers=headers, timeout=120, stream=True)
        
        # Fallback to master if main doesn't exist
        if resp.status_code == 404 and branch == "main":
            archive_url = f"{repo_url}/archive/refs/heads/master.zip"
            resp = requests.get(archive_url, headers=headers, timeout=120, stream=True)
            
        resp.raise_for_status()
        
        with open(archive_path, "wb") as f:
            for chunk in resp.iter_content(chunk_size=8192):
                f.write(chunk)
                
        print(f"[Trufy/Scanner] ✅ Downloaded archive ({archive_path.stat().st_size // 1024} KB)")

        # Extract it
        extract_dir = tmp_dir / "extracted"
        extract_dir.mkdir()
        with zipfile.ZipFile(archive_path, 'r') as zip_ref:
            zip_ref.extractall(extract_dir)

        # The extracted zip will have one top-level directory (e.g. repo-main)
        # We need to find it and zip its contents
        children = list(extract_dir.iterdir())
        if not children:
            print("[Trufy/Scanner] ❌ Extracted archive is empty")
            return None
            
        root_dir_to_zip = children[0]
        if not root_dir_to_zip.is_dir():
            # Unlikely, but if it extracted flat somehow, use the extract_dir
            root_dir_to_zip = extract_dir
            
        print(f"[Trufy/Scanner] Flattening and zipping contents of {root_dir_to_zip.name}...")
        shutil.make_archive(str(zip_path.with_suffix("")), "zip", root_dir_to_zip)
        
        print(f"[Trufy/Scanner] ✅ Re-zipped flat ({zip_path.stat().st_size // 1024} KB)")
        return zip_path

    except Exception as e:
        print(f"[Trufy/Scanner] ❌ Archive download/zip failed: {e}")

    shutil.rmtree(tmp_dir, ignore_errors=True)
    return None


# ── Full Scan Trigger ─────────────────────────────────────────────────────────

def trigger_trufy_scan(contest_db_id: int) -> bool:
    """
    Full pipeline: download repo → zip → upload to Trufy → create project → trigger scan.
    Saves the Trufy project ID to the DB for later result fetching.
    Returns True on success.
    """
    contest = get_contest(contest_db_id)
    if not contest:
        print(f"[Trufy/Scanner] ❌ Contest {contest_db_id} not found in DB.")
        return False

    title = contest.get("title", f"Contest-{contest_db_id}")
    code_url_raw = contest.get("code_url", "[]")

    # Check if already scanning
    existing_scan = get_trufy_scan_for_contest(contest_db_id)
    if existing_scan and existing_scan.get("status") in ("pending", "scanning", "completed", "done"):
        print(f"[Trufy/Scanner] ℹ️  Scan already exists for '{title}' ({existing_scan.get('status')}).")
        return False

    # Parse GitHub URLs
    github_infos = _extract_all_github_info(code_url_raw)
    if not github_infos:
        print(f"[Trufy/Scanner] ⚠️  No GitHub URLs found for '{title}' — skipping Trufy scan.")
        return False

    success_count = 0
    for repo_url, branch in github_infos:
        tmp_dir = None
        try:
            # Step 1: Download & zip
            zip_path = download_and_zip_repo(repo_url, branch)
            if not zip_path:
                continue
            tmp_dir = zip_path.parent

            # Step 2: Login to get JWT
            jwt = client.login()
            if not jwt:
                continue

            # Step 3: Upload zip to Trufy
            source_url = client.upload_zip(zip_path, jwt)
            if not source_url:
                continue

            # Step 4: Create project
            project_id = client.create_project(source_url, title, jwt)
            if not project_id:
                continue

            # Step 5: Trigger scan
            if not client.trigger_scan(project_id, jwt):
                continue

            # Step 6: Save to DB
            save_trufy_scan(contest_db_id, project_id, repo_url=repo_url)
            print(f"[Trufy/Scanner] ✅ Scan triggered for '{title}' ({repo_url}) (project={project_id}).")
            success_count += 1

        except Exception as e:
            print(f"[Trufy/Scanner] ❌ Unexpected error during scan trigger for {repo_url}: {e}")
        finally:
            if tmp_dir:
                shutil.rmtree(tmp_dir, ignore_errors=True)

    return success_count > 0


# ── Fetch Pending Results ─────────────────────────────────────────────────────

def _download_repo_unzipped(repo_url: str, branch: str = "main", dest_dir: Path = None) -> bool:
    """Downloads a GitHub archive and unzips it into the dest_dir without a top-level wrapper."""
    print(f"[Trufy/Scanner] Downloading repo for verification: {repo_url}")
    if dest_dir is None:
        dest_dir = Path(tempfile.mkdtemp(prefix="verify_repo_"))

    def _move_contents(src_dir: Path):
        for item in src_dir.iterdir():
            target = dest_dir / item.name
            if target.exists():
                if target.is_dir():
                    shutil.rmtree(target, ignore_errors=True)
                else:
                    target.unlink(missing_ok=True)
            shutil.move(str(item), str(dest_dir))

    def _clone_fallback() -> bool:
        clone_dir = dest_dir / "_git_clone"
        shutil.rmtree(clone_dir, ignore_errors=True)
        clone_url = repo_url
        if GITHUB_TOKEN and repo_url.startswith("https://github.com/"):
            clone_url = repo_url.replace("https://", f"https://x-access-token:{GITHUB_TOKEN}@", 1)

        branch_attempts = [branch]
        if branch == "main":
            branch_attempts.append("master")

        for branch_name in branch_attempts:
            cmd = ["git", "clone", "--depth", "1", "--branch", branch_name, clone_url, str(clone_dir)]
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
            if result.returncode != 0:
                shutil.rmtree(clone_dir, ignore_errors=True)
                continue

            git_dir = clone_dir / ".git"
            if git_dir.exists():
                shutil.rmtree(git_dir, ignore_errors=True)
            _move_contents(clone_dir)
            clone_dir.rmdir()
            return True

        return False

    try:
        import requests
        import zipfile

        headers = {"Authorization": f"token {GITHUB_TOKEN}"} if GITHUB_TOKEN else {}
        tmp_zip = dest_dir / "temp_archive.zip"
        archive_attempts = [branch]
        if branch == "main":
            archive_attempts.append("master")

        last_error = None
        for branch_name in archive_attempts:
            archive_url = f"{repo_url}/archive/refs/heads/{branch_name}.zip"
            for attempt in range(1, 4):
                try:
                    tmp_zip.unlink(missing_ok=True)
                    with requests.get(archive_url, headers=headers, timeout=(30, 180), stream=True) as resp:
                        if resp.status_code == 404:
                            break
                        resp.raise_for_status()
                        with open(tmp_zip, "wb") as f:
                            for chunk in resp.iter_content(chunk_size=1024 * 1024):
                                if chunk:
                                    f.write(chunk)
                    last_error = None
                    break
                except Exception as e:
                    last_error = e
                    print(f"[Trufy/Scanner] Archive download attempt {attempt}/3 failed: {e}")
            if tmp_zip.exists() and tmp_zip.stat().st_size > 0 and last_error is None:
                break

        if not tmp_zip.exists() or tmp_zip.stat().st_size == 0 or last_error is not None:
            print("[Trufy/Scanner] Archive download failed; trying git clone fallback...")
            if _clone_fallback():
                print("[Trufy/Scanner] ✅ Repo ready for verification via git clone fallback.")
                return True
            raise RuntimeError(last_error or "Archive download and git clone fallback failed")
                
        with zipfile.ZipFile(tmp_zip, 'r') as zip_ref:
            zip_ref.extractall(dest_dir)
            
        tmp_zip.unlink()
        
        # Flatten the structure if it has a single top-level folder
        children = [x for x in dest_dir.iterdir() if x.is_dir()]
        if len(children) == 1:
            root_dir = children[0]
            for item in root_dir.iterdir():
                shutil.move(str(item), str(dest_dir))
            root_dir.rmdir()
            
        print("[Trufy/Scanner] ✅ Repo ready for verification.")
        return True
    except Exception as e:
        print(f"[Trufy/Scanner] Archive setup failed: {e}")
        print("[Trufy/Scanner] Trying git clone fallback...")
        try:
            if _clone_fallback():
                print("[Trufy/Scanner] ✅ Repo ready for verification via git clone fallback.")
                return True
        except Exception as clone_error:
            print(f"[Trufy/Scanner] Git clone fallback failed: {clone_error}")
        print(f"[Trufy/Scanner] ❌ Failed to setup workspace: {e}")
        return False

TRIAGE_CANDIDATES_PER_CONTEST = int(os.environ.get(
    "TRIAGE_CANDIDATES_PER_CONTEST",
    str(MAX_POC_PER_CONTEST * 4)
))
POC_ATTEMPTS_PER_CONTEST = int(os.environ.get(
    "POC_ATTEMPTS_PER_CONTEST",
    str(MAX_POC_PER_CONTEST)
))

def _normalize_severity(finding: dict) -> str:
    raw = str(finding.get("severity") or finding.get("impact") or "medium").lower()
    severity_map = {
        "critical": "Critical", "high": "High",
        "medium": "Medium", "low": "Low", "info": "Low",
        "informational": "Low"
    }
    return severity_map.get(raw, "Medium")

def _finding_title(finding: dict) -> str:
    return (
        finding.get("check") or finding.get("name") or finding.get("slug") or
        finding.get("title") or finding.get("description", "")[:80] or "Untitled Finding"
    )

def _source_locations(finding: dict) -> list[dict]:
    locations = []
    for el in finding.get("elements", []) or []:
        sm = el.get("source_mapping", {}) if isinstance(el, dict) else {}
        filename = sm.get("filename_short") or sm.get("filename") or ""
        lines = sm.get("lines", []) or []
        start = lines[0] if len(lines) >= 1 else None
        end = lines[1] if len(lines) >= 2 else start
        script = (sm.get("script") or "").strip()
        if filename or script:
            locations.append({
                "filename": filename,
                "start": start,
                "end": end,
                "script": script,
            })
    return locations

def _build_issue_description(finding: dict) -> str:
    desc_parts = []
    if finding.get("description"):
        desc_parts.append(finding.get("description"))
    if finding.get("impact"):
        desc_parts.append(f"**Impact:**\n{finding.get('impact')}")
    if finding.get("recommendation"):
        desc_parts.append(f"**Recommendation:**\n{finding.get('recommendation')}")

    locations = _source_locations(finding)
    if locations:
        desc_parts.append("**Vulnerable Code:**")
        for loc in locations:
            line_str = ""
            if loc["start"] is not None and loc["end"] is not None:
                line_str = f"L{loc['start']}-L{loc['end']}"
            if loc["script"]:
                desc_parts.append(f"*{loc['filename'] or 'Unknown File'} {line_str}*:\n```solidity\n{loc['script']}\n```")

    return "\n\n".join(desc_parts)

def _normalized_words(text: str) -> set[str]:
    words = re.findall(r"[a-z0-9_]+", text.lower())
    stop = {
        "the", "a", "an", "and", "or", "of", "to", "in", "for", "with", "can",
        "may", "is", "are", "by", "from", "this", "that", "function", "contract"
    }
    return {w for w in words if len(w) > 2 and w not in stop}

def _impact_class(text: str) -> str:
    lowered = text.lower()
    classes = [
        ("loss", ("loss", "steal", "drain", "insolv", "undercharge", "overpay", "profit")),
        ("lock", ("lock", "freeze", "stuck", "unwithdraw", "permanent")),
        ("bypass", ("bypass", "unauthorized", "permission", "access control", "whitelist")),
        ("manipulation", ("manipulat", "oracle", "price", "slippage", "sandwich")),
        ("accounting", ("account", "round", "fee", "liquidity", "share", "debt", "collateral")),
        ("dos", ("revert", "denial", "dos", "grief")),
    ]
    for label, needles in classes:
        if any(n in lowered for n in needles):
            return label
    return "generic"

def _false_positive_reason(issue: dict, finding: dict) -> Optional[str]:
    title = str(issue.get("title") or "")
    description = str(issue.get("description") or "")
    text = f"{title}\n{description}".lower()

    if not title.strip() or title == "Untitled Finding":
        return "Missing a usable title."
    if len(description.strip()) < 80 and not _source_locations(finding):
        return "Finding lacks enough description or source evidence to evaluate."

    weak_categories = (
        "gas optimization", "gas saving", "missing event", "event emission",
        "natspec", "documentation", "typo", "style issue", "best practice",
        "code quality", "unused variable"
    )
    if any(p in text for p in weak_categories):
        return "Code-quality/gas/documentation issue without security impact."

    trust_model_markers = (
        "centralization", "trusted admin", "trusted owner", "admin privilege",
        "owner privilege", "privileged role", "onlyowner", "only owner",
        "admin can", "owner can"
    )
    if any(p in text for p in trust_model_markers) and not any(
        p in text for p in ("unauthorized", "any user", "permissionless", "attacker", "bypass")
    ):
        return "Depends on trusted/admin behavior rather than a permissionless exploit."

    if ("collision" in text or "truncat" in text) and not any(
        p in text for p in ("practical", "chosen", "overwrite", "existing pool", "existing position", "demonstrates")
    ):
        return "The collision/truncation claim is theoretical and lacks a realistic exploit path."

    if any(p in text for p in ("self-created", "own pool", "malformed pool", "malformed state", "invalid parameter")) and not any(
        p in text for p in ("victim", "third-party", "protocol loss", "steal", "drain", "insolv")
    ):
        return "Only describes malformed self-created state without third-party or protocol harm."

    if any(p in text for p in ("rounding", "precision", "dust")) and not any(
        p in text for p in ("profit", "loss", "drain", "insolv", "accumulate", "unbounded", "repeated")
    ):
        return "Rounding/dust claim lacks concrete loss or accumulable impact."

    return None

def _finding_score(issue: dict, finding: dict, skip_reason: Optional[str]) -> int:
    if skip_reason:
        return -100

    score = 0
    severity = issue.get("severity")
    confidence = str(issue.get("confidence") or "").lower()
    text = f"{issue.get('title', '')}\n{issue.get('description', '')}".lower()

    score += {"Critical": 45, "High": 35, "Medium": 20}.get(severity, 0)
    score += {"high": 20, "medium": 8, "low": -10}.get(confidence, 0)

    if _source_locations(finding):
        score += 15
    if re.search(r"\b(attacker|any user|malicious|victim|liquidator|trader)\b", text):
        score += 8
    if any(p in text for p in ("loss", "steal", "drain", "insolv", "permanent lock", "bypass", "profit")):
        score += 12
    if finding.get("_engine") == "codex":
        score += 5

    if any(p in text for p in ("missing validation", "can be set", "can call")) and not any(
        p in text for p in ("loss", "steal", "drain", "bypass", "insolv")
    ):
        score -= 10
    if any(p in text for p in ("theoretical", "collision", "centralization", "trusted", "admin")):
        score -= 8

    return score

def _line_overlap(a: dict, b: dict) -> bool:
    if not a.get("filename") or a.get("filename") != b.get("filename"):
        return False
    if a.get("start") is None or b.get("start") is None:
        return True
    return max(a["start"], b["start"]) <= min(a["end"], b["end"]) + 5

def _near_duplicate(a: dict, b: dict) -> bool:
    a_locs = _source_locations(a["finding"])
    b_locs = _source_locations(b["finding"])
    if any(_line_overlap(x, y) for x in a_locs for y in b_locs):
        return _impact_class(a["text"]) == _impact_class(b["text"])

    a_words = _normalized_words(a["issue"]["title"])
    b_words = _normalized_words(b["issue"]["title"])
    if not a_words or not b_words:
        return False
    overlap = len(a_words & b_words) / max(1, min(len(a_words), len(b_words)))
    return overlap >= 0.75 and _impact_class(a["text"]) == _impact_class(b["text"])

def _build_finding_record(finding: dict, project_id: str, contest_id: int) -> Optional[dict]:
    severity = _normalize_severity(finding)
    if severity not in ["Critical", "High", "Medium"]:
        return None

    finding_id = finding.get("id") or finding.get("issueId") or finding.get("_id")
    title = _finding_title(finding)
    description = _build_issue_description(finding)
    confidence = finding.get("confidence", "Medium")

    issue = {
        "contest_id": contest_id,
        "platform_issue_id": f"trufy-{project_id}-{finding_id}" if finding_id else None,
        "title": title,
        "description": description,
        "severity": severity,
        "classification": "trufy-ai",
        "status": "pending",
        "confidence": confidence
    }

    skip_reason = _false_positive_reason(issue, finding)
    score = _finding_score(issue, finding, skip_reason)
    text = f"{title}\n{description}"
    return {
        "finding": finding,
        "issue": issue,
        "skip_reason": skip_reason,
        "score": score,
        "text": text,
        "db_issue_id": None,
        "linked_record": None,
    }

def _rank_and_link_findings(findings: list, project_id: str, contest_id: int) -> list[dict]:
    records = []
    for finding in findings:
        record = _build_finding_record(finding, project_id, contest_id)
        if record:
            records.append(record)

    records.sort(key=lambda r: r["score"], reverse=True)
    representatives = []
    for record in records:
        if record["skip_reason"]:
            continue
        duplicate = next((rep for rep in representatives if _near_duplicate(rep, record)), None)
        if duplicate:
            record["linked_record"] = duplicate
            record["skip_reason"] = f"Near-duplicate of higher-ranked finding: {duplicate['issue']['title'][:100]}"
        else:
            representatives.append(record)

    return records

def fetch_pending_results():
    """
    Poll Trufy for results of all scans triggered >12h ago.
    Upserts findings, triggers agent verification for High/Medium issues.
    """
    pending = get_pending_trufy_scans()
    if not pending:
        print("[Trufy/Scanner] No pending scans ready for result fetching.")
        return

    print(f"[Trufy/Scanner] Fetching results for {len(pending)} completed scans...")
    jwt = client.login()
    if not jwt:
        print("[Trufy/Scanner] ❌ Cannot fetch results — authentication failed.")
        return

    for scan in pending:
        scan_id = scan["id"]
        contest_id = scan["contest_id"]
        project_id = scan["trufy_project_id"]
        title = scan.get("title", f"Contest-{contest_id}")
        
        contest_db = get_contest(contest_id)
        if not contest_db:
            continue
            
        # contests stores the source platform in `synced_from` (there is no
        # `platform` column), so reading "platform" here would always be "unknown"
        # and would silently disable platform-gated skills + report context.
        platform = contest_db.get("synced_from") or contest_db.get("platform") or "unknown"
        code_url_raw = contest_db.get("code_url", "[]")

        print(f"[Trufy/Scanner] Fetching results for '{title}' (project={project_id})...")
        is_finished, findings = client.get_scan_result_state(project_id, jwt)
        
        if not findings:
            if is_finished:
                mark_trufy_scan_done(scan_id, 0)
            else:
                print(f"[Trufy/Scanner] '{title}' is still scanning. Will poll again later.")
            continue
            
        repo_url = scan.get("repo_url")
        # Ensure we use a unique workspace per project to avoid clashes
        workspace_dir = Path(f"/tmp/workspace_{contest_id}_{project_id}")
        repo_downloaded = False
        count = 0
        poc_count = 0  # PoCs generated this contest (capped at MAX_POC_PER_CONTEST)
        poc_attempts = 0  # Findings sent to expensive PoC verification
        contest_description = contest_db.get("description", "")
        detected_framework = ""
        triage_skill_context = ""
        poc_skill_context = ""

        ranked_findings = _rank_and_link_findings(findings, project_id, contest_id)
        print(
            f"[Trufy/Scanner] Ranked {len(ranked_findings)} High/Medium findings "
            f"from {len(findings)} raw Trufy results."
        )

        try:
            triage_attempts = 0

            for record in ranked_findings:
                issue_dict = record["issue"]
                db_issue_id = upsert_issue(issue_dict)
                record["db_issue_id"] = db_issue_id
                count += 1

                if record["skip_reason"]:
                    linked_id = None
                    if record.get("linked_record") and record["linked_record"].get("db_issue_id"):
                        linked_id = record["linked_record"]["db_issue_id"]
                    print(f"[Trufy/Scanner] 🚫 Prefilter SKIP issue {db_issue_id}: {record['skip_reason']}")
                    update_issue_triage_skip(db_issue_id, record["skip_reason"], linked_issue_id=linked_id)
                    continue

                # --- Verification Loop ---
                if poc_count >= MAX_POC_PER_CONTEST:
                    continue
                if poc_attempts >= POC_ATTEMPTS_PER_CONTEST:
                    continue
                if triage_attempts >= TRIAGE_CANDIDATES_PER_CONTEST:
                    continue

                if not repo_downloaded and repo_url:
                    workspace_dir.mkdir(parents=True, exist_ok=True)
                    repo_downloaded = _download_repo_unzipped(repo_url, "main", workspace_dir)

                if not repo_downloaded:
                    continue
                if not detected_framework:
                    detected_framework = detect_framework(workspace_dir).framework
                    triage_skill_context = get_selected_skill_context(
                        contest_id,
                        phase="triage",
                        platform=platform,
                        framework=detected_framework,
                    )
                    poc_skill_context = get_selected_skill_context(
                        contest_id,
                        phase="poc",
                        platform=platform,
                        framework=detected_framework,
                    )

                triage_attempts += 1
                print(
                    f"[Trufy/Scanner] Triage candidate {triage_attempts}/{TRIAGE_CANDIDATES_PER_CONTEST} "
                    f"issue {db_issue_id} score={record['score']}: {issue_dict.get('title', '')[:100]}"
                )
                try:
                    should_proceed, triage_reason = triage_issue(issue_dict, contest_description, triage_skill_context)
                    if not should_proceed:
                        print(f"[Trufy/Scanner] 🚫 Triage SKIP issue {db_issue_id}: {triage_reason}")
                        update_issue_triage_skip(db_issue_id, triage_reason)
                    else:
                        print(f"[Trufy/Scanner] ✅ Triage PASS issue {db_issue_id}: {triage_reason}")
                        poc_attempts += 1
                        print(
                            f"[Trufy/Scanner] PoC attempt {poc_attempts}/{POC_ATTEMPTS_PER_CONTEST} "
                            f"for issue {db_issue_id}"
                        )
                        outcome = verify_issue_with_agent(issue_dict, workspace_dir, contest_description, poc_skill_context)
                        if outcome.verified:
                            poc_count += 1
                            print(f"[Trufy/Scanner] Agent successfully verified issue {db_issue_id} ({poc_count}/{MAX_POC_PER_CONTEST} PoCs)")
                            report_skill_context = get_selected_skill_context(
                                contest_id,
                                phase="report",
                                platform=platform,
                                framework=outcome.framework,
                            )
                            report_md = generate_submission_report(
                                issue_dict,
                                outcome.poc_code,
                                platform,
                                framework=outcome.framework,
                                verification_command=outcome.command,
                                skill_context=report_skill_context,
                            )
                            update_issue_verification(db_issue_id, outcome.poc_code, report_md)
                        elif outcome.status in ("invalid", "unsupported"):
                            heading = "PoC Verification Unsupported" if outcome.status == "unsupported" else "PoC Verification Failed"
                            update_issue_status_reason(db_issue_id, outcome.status, heading, outcome.reason)
                            print(f"[Trufy/Scanner] Agent marked issue {db_issue_id} as {outcome.status}: {outcome.reason}")
                        else:
                            print(f"[Trufy/Scanner] Agent verification failed for issue {db_issue_id}: {outcome.reason}")
                except Exception as e:
                    # One bad finding must not abort the whole result-fetch cycle, or the
                    # scan never gets marked done and re-polls the same poison finding forever.
                    print(f"[Trufy/Scanner] ❌ Error verifying issue {db_issue_id}: {e}")
                    add_notification(contest_id, f"System Error during verification: {e}", db_issue_id)

            if is_finished:
                mark_trufy_scan_done(scan_id, count)
                if count > 0:
                    add_notification(
                        contest_id,
                        f"Trufy AI scan complete: {count} issues found. High/Medium issues processed by Agent."
                    )
                print(f"[Trufy/Scanner] ✅ '{title}': Scan complete. {count} findings processed.")
            else:
                print(f"[Trufy/Scanner] 🔄 '{title}': Still scanning. Processed {count} available findings. Will poll again.")
        finally:
            # Cleanup
            if workspace_dir.exists():
                shutil.rmtree(workspace_dir, ignore_errors=True)
                print(f"[Trufy/Scanner] 🧹 Cleaned up temporary workspace: {workspace_dir}")

def verify_single_issue(issue_id: int):
    """
    Manually run the agentic verification pipeline for a single issue.
    Used when the user clicks 'Verify & Generate PoC' on one issue in the UI.
    """
    from shared.db import _conn
    print(f"[Trufy/Scanner] Manually verifying issue {issue_id}...")
    
    with _conn() as cur:
        cur.execute("""
            SELECT i.*, c.code_url, c.synced_from AS platform, c.description AS contest_description
            FROM issues i 
            JOIN contests c ON i.contest_id = c.id 
            WHERE i.id = %s
        """, (issue_id,))
        issue_row = cur.fetchone()
        
    if not issue_row:
        print(f"[Trufy/Scanner] ❌ Issue {issue_id} not found.")
        return
        
    issue_dict = dict(issue_row)
    code_url_raw = issue_dict.pop("code_url", "[]")
    platform = issue_dict.pop("platform", "unknown")
    contest_description = issue_dict.pop("contest_description", "") or ""
    
    workspace_dir = Path(f"/tmp/workspace_{issue_dict['contest_id']}_{issue_id}")
    workspace_dir.mkdir(parents=True, exist_ok=True)
    
    github_info = _extract_github_info(code_url_raw)
    if not github_info:
        print(f"[Trufy/Scanner] ❌ No valid github repo found for contest {issue_dict['contest_id']}")
        return
        
    repo_url, branch = github_info
    
    try:
        if _download_repo_unzipped(repo_url, branch, workspace_dir):
            detected_framework = detect_framework(workspace_dir).framework
            poc_skill_context = get_selected_skill_context(
                issue_dict["contest_id"],
                phase="poc",
                platform=platform,
                framework=detected_framework,
            )
            outcome = verify_issue_with_agent(issue_dict, workspace_dir, contest_description, poc_skill_context)
            if outcome.verified:
                print(f"[Trufy/Scanner] ✅ Agent successfully verified issue {issue_id}")
                report_skill_context = get_selected_skill_context(
                    issue_dict["contest_id"],
                    phase="report",
                    platform=platform,
                    framework=outcome.framework,
                )
                report_md = generate_submission_report(
                    issue_dict,
                    outcome.poc_code,
                    platform,
                    framework=outcome.framework,
                    verification_command=outcome.command,
                    skill_context=report_skill_context,
                )
                update_issue_verification(issue_id, outcome.poc_code, report_md)
            elif outcome.status in ("invalid", "unsupported"):
                heading = "PoC Verification Unsupported" if outcome.status == "unsupported" else "PoC Verification Failed"
                update_issue_status_reason(issue_id, outcome.status, heading, outcome.reason)
                add_notification(issue_dict['contest_id'], f"Agent marked issue {outcome.status}: {outcome.reason}", issue_id)
            else:
                print(f"[Trufy/Scanner] ❌ Agent could not verify issue {issue_id}")
                add_notification(issue_dict['contest_id'], f"Agent verification failed: {outcome.reason}", issue_id)
    finally:
        if workspace_dir.exists():
            shutil.rmtree(workspace_dir, ignore_errors=True)
            print(f"[Trufy/Scanner] 🧹 Cleaned up temporary workspace: {workspace_dir}")


def batch_verify_issues(job_id: str, issue_ids: list):
    """
    Verify multiple issues as a background job.

    Groups issues by contest_id so the repo is downloaded ONCE per contest
    regardless of how many issues belong to it. Updates the job's per-issue
    results in the DB as each issue completes so the UI can show live progress.

    Called by the worker when it picks up a 'batch_verify' job from Redis.
    """
    import redis as _redis
    from shared.db import _conn

    _redis_host = os.environ.get("REDIS_HOST", "redis")
    _redis_port = int(os.environ.get("REDIS_PORT", "6381"))
    r = _redis.Redis(host=_redis_host, port=_redis_port, decode_responses=True)

    print(f"[Trufy/Scanner] Starting batch job {job_id} for {len(issue_ids)} issues...")
    finish_verification_job(job_id, status="running")

    # ── 1. Load all issue rows and group by contest_id ────────────────────────
    groups: dict[int, list] = {}   # contest_id → [issue_dict, ...]
    platforms: dict[int, str] = {}
    code_urls: dict[int, str] = {}
    contest_descriptions: dict[int, str] = {}

    with _conn() as cur:
        cur.execute("""
            SELECT i.*, c.code_url, c.synced_from AS platform, c.description AS contest_description
            FROM issues i
            JOIN contests c ON i.contest_id = c.id
            WHERE i.id = ANY(%s)
        """, (issue_ids,))
        rows = cur.fetchall()

    for row in rows:
        d = dict(row)
        cid = d["contest_id"]
        code_url_raw = d.pop("code_url", "[]")
        platform = d.pop("platform", "unknown")
        contest_description = d.pop("contest_description", "") or ""
        groups.setdefault(cid, []).append(d)
        platforms[cid] = platform
        code_urls[cid] = code_url_raw
        contest_descriptions[cid] = contest_description

    # Mark any issue_ids not found as failed
    found_ids = {row["id"] for row in rows}
    for missing_id in set(issue_ids) - found_ids:
        update_job_issue_result(job_id, missing_id, {"status": "failed", "error": "Issue not found"})

    overall_status = "done"

    # ── 2. Process each contest group ─────────────────────────────────────────
    for contest_id, issues in groups.items():
        github_info = _extract_github_info(code_urls[contest_id])
        if not github_info:
            for issue in issues:
                update_job_issue_result(job_id, issue["id"], {
                    "status": "failed", "error": "No GitHub repo URL found for this contest"
                })
            overall_status = "done"  # partial failure is still 'done'
            continue

        repo_url, branch = github_info
        workspace_dir = Path(f"/tmp/batchjob_{job_id}_{contest_id}")
        workspace_dir.mkdir(parents=True, exist_ok=True)

        # ── Redis lock: prevents two concurrent jobs from downloading the same repo
        lock_key = f"lock:workspace:{contest_id}"
        # SET NX EX: only set if Not eXists, expire in 15 minutes
        acquired = r.set(lock_key, job_id, nx=True, ex=900)
        if not acquired:
            # Another job is already downloading this contest — wait briefly then proceed
            print(f"[Trufy/Scanner] Waiting for existing download lock on contest {contest_id}...")
            import time
            for _ in range(30):   # wait up to 5 min (30 x 10s)
                time.sleep(10)
                if not r.exists(lock_key):
                    break
            # Try to take ownership now that it's (hopefully) free. If another job
            # grabbed it first, acquired stays falsy and we won't touch its lock below.
            acquired = r.set(lock_key, job_id, nx=True, ex=900)

        try:
            print(f"[Trufy/Scanner] Downloading contest {contest_id} for {len(issues)} issues...")
            downloaded = _download_repo_unzipped(repo_url, branch, workspace_dir)

            if not downloaded:
                for issue in issues:
                    update_job_issue_result(job_id, issue["id"], {
                        "status": "failed", "error": "Failed to download repository"
                })
                continue

            detected_framework = detect_framework(workspace_dir).framework
            triage_skill_context = get_selected_skill_context(
                contest_id,
                phase="triage",
                platform=platforms[contest_id],
                framework=detected_framework,
            )
            poc_skill_context = get_selected_skill_context(
                contest_id,
                phase="poc",
                platform=platforms[contest_id],
                framework=detected_framework,
            )

            # ── 3. Run triage + PoC for every issue in this contest group ─────
            for issue in issues:
                issue_id = issue["id"]
                print(f"[Trufy/Scanner] [{job_id}] Processing issue {issue_id}: {issue.get('title', '')}")
                update_job_issue_result(job_id, issue_id, {"status": "verifying"})

                try:
                    should_proceed, triage_reason = triage_issue(
                        issue,
                        contest_descriptions.get(contest_id, ""),
                        triage_skill_context,
                    )
                    if not should_proceed:
                        update_job_issue_result(job_id, issue_id, {
                            "status": "skipped", "reason": triage_reason
                        })
                        add_notification(contest_id, f"Agent Triage Skipped: {triage_reason}", issue_id)
                        continue

                    outcome = verify_issue_with_agent(
                        issue,
                        workspace_dir,
                        contest_descriptions.get(contest_id, ""),
                        poc_skill_context,
                    )
                    result_payload = {
                        "status": outcome.status,
                        "reason": outcome.reason,
                        "framework": outcome.framework,
                        "attempts": outcome.attempts,
                        "command": outcome.command,
                        "stdout": outcome.stdout[-4000:] if outcome.stdout else "",
                        "stderr": outcome.stderr[-4000:] if outcome.stderr else "",
                    }

                    if outcome.verified:
                        report_skill_context = get_selected_skill_context(
                            contest_id,
                            phase="report",
                            platform=platforms[contest_id],
                            framework=outcome.framework,
                        )
                        report_md = generate_submission_report(
                            issue,
                            outcome.poc_code,
                            platforms[contest_id],
                            framework=outcome.framework,
                            verification_command=outcome.command,
                            skill_context=report_skill_context,
                        )
                        update_issue_verification(issue_id, outcome.poc_code, report_md)
                        update_job_issue_result(job_id, issue_id, {
                            "status": "verified",
                            "poc_code": outcome.poc_code,
                            "framework": outcome.framework,
                            "attempts": outcome.attempts,
                            "command": outcome.command,
                        })
                        print(f"[Trufy/Scanner] ✅ [{job_id}] Issue {issue_id} verified.")
                    elif outcome.status in ("invalid", "unsupported"):
                        heading = "PoC Verification Unsupported" if outcome.status == "unsupported" else "PoC Verification Failed"
                        update_issue_status_reason(issue_id, outcome.status, heading, outcome.reason)
                        update_job_issue_result(job_id, issue_id, result_payload)
                        add_notification(contest_id, f"Agent marked issue {outcome.status}: {outcome.reason}", issue_id)
                    else:
                        result_payload["status"] = "failed"
                        update_job_issue_result(job_id, issue_id, result_payload)
                        add_notification(contest_id, f"Agent verification failed: {outcome.reason}", issue_id)

                except Exception as e:
                    print(f"[Trufy/Scanner] ❌ [{job_id}] Error on issue {issue_id}: {e}")
                    update_job_issue_result(job_id, issue_id, {
                        "status": "failed", "error": str(e)
                    })
                    add_notification(contest_id, f"System Error during verification: {e}", issue_id)

        finally:
            # Only release the Redis lock if we actually own it (don't delete a
            # lock that a different concurrent job holds), then delete the workspace.
            if acquired:
                try:
                    if r.get(lock_key) == job_id:
                        r.delete(lock_key)
                except Exception:
                    pass
            if workspace_dir.exists():
                shutil.rmtree(workspace_dir, ignore_errors=True)
                print(f"[Trufy/Scanner] 🧹 [{job_id}] Cleaned up workspace for contest {contest_id}.")

    finish_verification_job(job_id, status=overall_status)
    print(f"[Trufy/Scanner] ✅ Batch job {job_id} complete.")



# ── CLI Entry Point ───────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Trufy Scanner")
    parser.add_argument("--test-login", action="store_true", help="Test Trufy authentication")
    parser.add_argument("--fetch-results", action="store_true", help="Fetch pending scan results now")
    parser.add_argument("--scan", type=int, metavar="CONTEST_DB_ID", help="Trigger a Trufy scan for a contest")
    args = parser.parse_args()

    if args.test_login:
        import json
        jwt = client.login()
        if jwt:
            print(f"✅ Login successful. JWT (first 40 chars): {jwt[:40]}...")
        else:
            print("❌ Login failed. Check TRUFY_EMAIL and TRUFY_PASSWORD in .env")
    elif args.fetch_results:
        fetch_pending_results()
    elif args.scan is not None:
        ok = trigger_trufy_scan(args.scan)
        print("✅ Scan triggered." if ok else "❌ Scan failed.")
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
