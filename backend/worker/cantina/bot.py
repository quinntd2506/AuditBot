import os
import requests
import sys
from pathlib import Path
from datetime import datetime, timezone
from typing import Optional

# Add worker root to path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from shared.db import upsert_contest

PLATFORM = "cantina"
CANTINA_API = "https://api.cantina.xyz"

def _make_session() -> requests.Session:
    session = requests.Session()
    session.headers.update({
        "accept": "*/*",
        "origin": "https://cantina.xyz",
        "user-agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/148.0.0.0 Safari/537.36"
    })
    
    # Use auth token if provided in .env (for private contests)
    token = os.environ.get("CANTINA_AUTH_TOKEN")
    if token:
        session.cookies.set("auth_token", token, domain=".cantina.xyz")
    return session

def _parse_datetime(dt_str: Optional[str]) -> Optional[datetime]:
    if not dt_str:
        return None
    try:
        from dateutil.parser import parse
        return parse(dt_str)
    except Exception:
        return None

def _map_status(status: str, end_dt: Optional[datetime]) -> str:
    # Cantina statuses: live, escalations_ended, completed, etc.
    if status == "live":
        return "active"
    elif status == "upcoming":
        return "upcoming"
    else:
        now = datetime.now(timezone.utc)
        if end_dt and end_dt < now:
            return "completed"
        return "active"

def sync():
    """Fetches contests from Cantina and syncs them to the DB."""
    session = _make_session()
    
    url = f"{CANTINA_API}/api/v0/opportunities"
    params = {
        "limit": 100,
        "kind": "public_contest,private_contest",
        "status": "live"
    }
    
    items = []
    for attempt in range(3):
        try:
            resp = session.get(url, params=params, timeout=15)
            resp.raise_for_status()
            data = resp.json()
            items = data.get("items", [])
            break
        except Exception as e:
            if attempt == 2:
                print(f"[{PLATFORM.capitalize()}] ❌ Failed to fetch contests after 3 attempts: {e}")
                return
            print(f"[{PLATFORM.capitalize()}] ⚠️  Attempt {attempt+1} failed: {e}. Retrying...")
            import time
            time.sleep(2)

    for it in items:
        try:
            pid = it.get("id")
            if not pid:
                continue

            platform_id = f"{PLATFORM}-{pid}"
            title = it.get("name", "")
            description = it.get("instructions", "")
            
            timeframe = it.get("timeframe", {})
            starts_at = timeframe.get("start")
            ends_at = timeframe.get("end")
            
            start_dt = _parse_datetime(starts_at)
            end_dt = _parse_datetime(ends_at)
            
            raw_status = it.get("status", "")
            status = _map_status(raw_status, end_dt)
            
            try:
                prize_pool = float(it.get("totalRewardPot", 0))
            except (ValueError, TypeError):
                prize_pool = 0
                
            token = it.get("currencyCode", "USDC")
            
            company = it.get("company", {})
            logo_url = company.get("logo", "")
            
            contest_url = f"https://cantina.xyz/competitions/{pid}"
            
            is_finalized = raw_status in {"escalations_ended", "completed"}

            import re
            import json
            code_urls = []
            github_matches = re.findall(r'https://github\.com/([a-zA-Z0-9_-]+/[a-zA-Z0-9_-]+)', description)
            if github_matches:
                # Deduplicate while preserving order
                seen = set()
                code_urls = [f"https://github.com/{m}" for m in github_matches if not (m in seen or seen.add(m))]
            code_url_str = json.dumps(code_urls) if code_urls else ""

            upsert_params = {
                "platform_contest_id": platform_id,
                "title": title,
                "platform_url": contest_url,
                "code_url": code_url_str,
                "description": description,
                "status": status,
                "prize_pool": prize_pool,
                "token": token,
                "logo_url": logo_url,
                "starts_at": starts_at,
                "ends_at": ends_at,
                "synced_from": PLATFORM,
                "is_finalized": is_finalized,
                "metadata": {
                    "cantina_status": raw_status,
                    "company_name": company.get("name", ""),
                    "kyc_required": it.get("kycRequired", False),
                    "total_findings": it.get("totalFindings", 0)
                }
            }
            
            upsert_contest(upsert_params)
            
        except Exception as e:
            print(f"[{PLATFORM.capitalize()}] ⚠️ Failed to process contest {it.get('name', 'Unknown')}: {e}")

    print(f"[{PLATFORM.capitalize()}] ✅ Synced {len(items)} contests")

def check_responses():
    """
    Cantina does not have a direct 'submissions' API yet.
    """
    pass

if __name__ == "__main__":
    sync()
