import time
import json
import redis
import os
import sys
from datetime import datetime, timezone

REDIS_URL = os.environ.get("REDIS_URL")
if not REDIS_URL:
    raise RuntimeError("REDIS_URL environment variable is not set")
redis_client = redis.Redis.from_url(REDIS_URL, decode_responses=True)

STATUS_UPDATE_INTERVAL_SECONDS = 600
CONTEST_DISCOVERY_INTERVAL_SECONDS = 86400
RESPONSE_CHECK_INTERVAL_SECONDS = 43200
TRUFY_RESULT_FETCH_INTERVAL_SECONDS = 3600

# Try to import bot logics. 
# They will be adjusted to run as functions since they are now migrated to backend/worker/.
def get_sherlock_bot():
    try:
        from worker.sherlock import bot
        return bot
    except (ImportError, ModuleNotFoundError):
        try:
            from sherlock import bot
            return bot
        except ImportError:
            return None

def get_guardian_bot():
    try:
        from worker.guardian import bot
        return bot
    except (ImportError, ModuleNotFoundError):
        try:
            from guardian import bot
            return bot
        except ImportError:
            return None

def get_code4rena_bot():
    try:
        from worker.code4rena import bot
        return bot
    except (ImportError, ModuleNotFoundError):
        try:
            from code4rena import bot
            return bot
        except ImportError:
            return None

sherlock_bot = get_sherlock_bot()
guardian_bot = get_guardian_bot()
code4rena_bot = get_code4rena_bot()

def get_immunefi_bot():
    try:
        from worker.immunefi import bot
        return bot
    except (ImportError, ModuleNotFoundError):
        try:
            from immunefi import bot
            return bot
        except ImportError:
            return None

def get_cantina_bot():
    try:
        from worker.cantina import bot
        return bot
    except (ImportError, ModuleNotFoundError):
        try:
            from cantina import bot
            return bot
        except ImportError:
            return None

immunefi_bot = get_immunefi_bot()
cantina_bot = get_cantina_bot()

if not sherlock_bot:
    print("[Worker] ⚠️  Sherlock bot logic not found!")
if not guardian_bot:
    print("[Worker] ⚠️  Guardian bot logic not found!")
if not code4rena_bot:
    print("[Worker] ⚠️  Code4rena bot logic not found!")
if not immunefi_bot:
    print("[Worker] ⚠️  Immunefi bot logic not found!")

# Trufy scanner
try:
    from worker.trufy import scanner as trufy_scanner
except ImportError as e:
    trufy_scanner = None
    print(f"[Worker] ⚠️  Trufy scanner not found! Error: {e}")

def _as_utc_datetime(value):
    if not value:
        return None
    if isinstance(value, str):
        try:
            value = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except Exception:
            return None
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)

def _contest_can_be_joined(contest_id: int) -> tuple[bool, str]:
    from worker.shared.db import get_contest
    contest = get_contest(contest_id)
    if not contest:
        return False, "contest not found"

    now = datetime.now(timezone.utc)
    starts_at = contest.get("starts_at")
    ends_at = contest.get("ends_at")

    start_dt = _as_utc_datetime(starts_at)
    end_dt = _as_utc_datetime(ends_at)
    if start_dt:
        if start_dt > now:
            return False, "contest has not started"
    if end_dt:
        if end_dt <= now:
            return False, "contest has ended"

    if contest.get("has_joined"):
        return False, "already joined"
    if contest.get("skip_autojoin"):
        return False, "auto-join disabled"

    return True, ""

def handle_job(job):
    print(f"[Worker] Handling job: {job}")
    try:
        data = json.loads(job)
        action = data.get("action")

        if action == "sync_all":
            # Guardian Sync
            if guardian_bot and hasattr(guardian_bot, 'sync'):
                try:
                    token = guardian_bot.login()
                    if token:
                        # Accommodate both _make_session and make_session
                        sess_func = getattr(guardian_bot, '_make_session', None) or getattr(guardian_bot, 'make_session', None)
                        if sess_func:
                            sess = sess_func(token)
                            guardian_bot.sync(sess)
                except Exception as e:
                    print(f"[Worker] Error running Guardian bot sync: {e}")
            # Sherlock sync
            if sherlock_bot and hasattr(sherlock_bot, 'sync'):
                try:
                    sess = sherlock_bot.build_session()
                    sherlock_bot.sync(sess)
                except Exception as e:
                    print(f"[Worker] Error running Sherlock bot sync: {e}")
            # Code4rena Sync
            if code4rena_bot and hasattr(code4rena_bot, 'sync'):
                try:
                    token = code4rena_bot.login()
                    if token:
                        sess_func = getattr(code4rena_bot, '_make_session', None)
                        if sess_func:
                            sess = sess_func(token)
                            code4rena_bot.sync(sess)
                except Exception as e:
                    print(f"[Worker] Error running Code4rena bot sync: {e}")
            # Immunefi Sync (public — no login needed for discovery)
            if immunefi_bot and hasattr(immunefi_bot, 'sync'):
                try:
                    immunefi_bot.sync()
                except Exception as e:
                    print(f"[Worker] Error running Immunefi bot sync: {e}")
            # Cantina Sync
            if cantina_bot and hasattr(cantina_bot, 'sync'):
                try:
                    cantina_bot.sync()
                except Exception as e:
                    print(f"[Worker] Error running Cantina bot sync: {e}")

        elif action == "ping_contest":
            platform = data.get("platform", "").lower()
            cid = data.get("contest_id")
            force = data.get("force", False)
            if platform == "guardian" and guardian_bot and hasattr(guardian_bot, 'check_responses'):
                token = guardian_bot.login()
                if token:
                    sess_func = getattr(guardian_bot, '_make_session', None)
                    if sess_func:
                        sess = sess_func(token)
                        guardian_bot.check_responses(sess, cid, force=force)
            elif platform == "sherlock" and sherlock_bot and hasattr(sherlock_bot, 'check_responses'):
                sherlock_bot.check_responses(cid, force=force)
            elif platform == "code4rena" and code4rena_bot and hasattr(code4rena_bot, 'check_responses'):
                token = code4rena_bot.login()
                if token:
                    sess_func = getattr(code4rena_bot, '_make_session', None)
                    if sess_func:
                        sess = sess_func(token)
                        code4rena_bot.check_responses(sess, cid, force=force)
            elif platform == "immunefi" and immunefi_bot and hasattr(immunefi_bot, 'check_responses'):
                immunefi_bot.check_responses(cid, force=force)

        elif action == "check_responses_all":
            from worker.shared.db import get_contests_for_response_check
            contests = get_contests_for_response_check()
            print(f"[Worker] Checking judge responses for {len(contests)} contests...")
            for contest in contests:
                redis_client.lpush("jobs", json.dumps({
                    "action": "ping_contest",
                    "contest_id": contest["id"],
                    "platform": contest["synced_from"],
                    "force": False
                }))

        elif action == "join_contest":
            from worker.shared.db import mark_joined
            platform = data.get("platform", "").lower()
            cid = data.get("contest_id")
            can_join, reason = _contest_can_be_joined(cid)
            if not can_join:
                print(f"[Worker] Skipping join for contest {cid}: {reason}")
                return
            if platform == "guardian" and guardian_bot and hasattr(guardian_bot, 'join'):
                token = guardian_bot.login()
                if token:
                    sess_func = getattr(guardian_bot, '_make_session', None)
                    if sess_func:
                        sess = sess_func(token)
                        if guardian_bot.join(sess, cid):
                            mark_joined(cid)
            elif platform == "sherlock" and sherlock_bot and hasattr(sherlock_bot, 'join'):
                sess = sherlock_bot.build_session()
                if sherlock_bot.join(sess, cid):
                    mark_joined(cid)
            elif platform == "code4rena" and code4rena_bot and hasattr(code4rena_bot, 'join'):
                token = code4rena_bot.login()
                if token:
                    sess_func = getattr(code4rena_bot, '_make_session', None)
                    if sess_func:
                        sess = sess_func(token)
                        if code4rena_bot.join(sess, cid):
                            mark_joined(cid)
            elif platform == "immunefi" and immunefi_bot and hasattr(immunefi_bot, 'join'):
                # Auto-join disabled for Immunefi; join is manual on the platform
                immunefi_bot.join(None, cid)

        elif action == "trufy_scan":
            cid = data.get("contest_id")
            if cid and trufy_scanner:
                trufy_scanner.trigger_trufy_scan(cid)
            else:
                print(f"[Worker] ⚠️  trufy_scan job missing contest_id or scanner unavailable.")
                
        elif action == "verify_issue":
            issue_id = data.get("issue_id")
            if issue_id and trufy_scanner:
                trufy_scanner.verify_single_issue(issue_id)
            else:
                print(f"[Worker] ⚠️  verify_issue job missing issue_id or scanner unavailable.")

        elif action == "batch_verify":
            job_id   = data.get("job_id")
            issue_ids = data.get("issue_ids", [])
            if job_id and issue_ids and trufy_scanner:
                trufy_scanner.batch_verify_issues(job_id, issue_ids)
            else:
                print(f"[Worker] ⚠️  batch_verify job missing job_id/issue_ids or scanner unavailable.")

    except Exception as e:
        print(f"[Worker] Error handling job: {e}")

def update_status_loop():
    """Periodically push platform status to Redis so the API can read it."""
    if guardian_bot and hasattr(guardian_bot, 'status') and hasattr(guardian_bot, 'login'):
        try:
            token = guardian_bot.login()
            sess_func = getattr(guardian_bot, '_make_session', None)
            sess = sess_func(token) if token and sess_func else None
            st = guardian_bot.status(token, sess)
            redis_client.set("status_guardian", json.dumps(st))
        except Exception as e:
            print(f"[Worker] ⚠️  Guardian status update failed: {e}")
    if sherlock_bot and hasattr(sherlock_bot, 'status'):
        try:
            sess = sherlock_bot.build_session()
            st = sherlock_bot.status(sess)
            redis_client.set("status_sherlock", json.dumps(st))
        except Exception as e:
            print(f"[Worker] ⚠️  Sherlock status update failed: {e}")
    if code4rena_bot and hasattr(code4rena_bot, 'status') and hasattr(code4rena_bot, 'login'):
        try:
            token = code4rena_bot.login()
            sess_func = getattr(code4rena_bot, '_make_session', None)
            sess = sess_func(token) if token and sess_func else None
            st = code4rena_bot.status(token, sess)
            redis_client.set("status_code4rena", json.dumps(st))
        except Exception as e:
            print(f"[Worker] ⚠️  Code4rena status update failed: {e}")
    if immunefi_bot and hasattr(immunefi_bot, 'status'):
        try:
            st = immunefi_bot.status()
            redis_client.set("status_immunefi", json.dumps(st))
        except Exception as e:
            print(f"[Worker] ⚠️  Immunefi status update failed: {e}")
    if cantina_bot and hasattr(cantina_bot, 'status'):
        try:
            st = cantina_bot.status()
            redis_client.set("status_cantina", json.dumps(st))
        except Exception as e:
            print(f"[Worker] ⚠️  Cantina status update failed: {e}")
        
def ping_all_active_contests():
    """Trigger a response check for all contests that are not finalized."""
    redis_client.lpush("jobs", json.dumps({"action": "check_responses_all"}))

def _scheduler_due(key: str, interval_seconds: int, now: float) -> bool:
    try:
        last_run = float(redis_client.get(key) or 0)
    except Exception:
        last_run = 0
    return now - last_run > interval_seconds

def _mark_scheduler_run(key: str, now: float):
    redis_client.set(key, str(now))

def main():
    print("🤖 Worker Daemon Started!")
    
    while True:
        try:
            # Check for jobs blocking for up to 10 seconds
            result = redis_client.blpop("jobs", timeout=10)
            if result:
                _, job_data = result
                handle_job(job_data)
            
            now = time.time()

            # 1. Periodically update bot auth status in redis (every ~10 mins)
            if _scheduler_due("scheduler:last_status_update", STATUS_UPDATE_INTERVAL_SECONDS, now):
                update_status_loop()
                _mark_scheduler_run("scheduler:last_status_update", now)

            # 2. Daily Sync All (every 24 hours = 86400s)
            if _scheduler_due("scheduler:last_sync_all", CONTEST_DISCOVERY_INTERVAL_SECONDS, now):
                print("[Worker] ⏰ Running daily contest discovery...")
                redis_client.lpush("jobs", json.dumps({"action": "sync_all"}))
                _mark_scheduler_run("scheduler:last_sync_all", now)

            # 3. Check Judge Responses (every 12 hours = 43200s)
            if _scheduler_due("scheduler:last_check_responses", RESPONSE_CHECK_INTERVAL_SECONDS, now):
                print("[Worker] ⏰ Checking judge responses for active contests...")
                ping_all_active_contests()
                _mark_scheduler_run("scheduler:last_check_responses", now)

            # 4. Fetch Trufy scan results (every 1 hour = 3600s)
            if trufy_scanner and _scheduler_due("scheduler:last_trufy_result_fetch", TRUFY_RESULT_FETCH_INTERVAL_SECONDS, now):
                print("[Worker] ⏰ Running periodic Trufy result fetch...")
                trufy_scanner.fetch_pending_results()
                _mark_scheduler_run("scheduler:last_trufy_result_fetch", now)
                
        except Exception as e:
            print(f"[Worker] Crash: {e}")
            time.sleep(5)

if __name__ == "__main__":
    print("[Worker] 🚀 Worker daemon starting...")
    print(f"[Worker] 📡 Connectivity: Redis={REDIS_URL}")
    
    # Try to ping redis
    try:
        redis_client.ping()
        print("[Worker] ✅ Redis connection successful.")
    except Exception as e:
        print(f"[Worker] ❌ Redis connection failed: {e}")
        sys.exit(1)

    main()
