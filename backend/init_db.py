import psycopg2
import psycopg2.extras
import os
import sys
import json
from pathlib import Path
from dotenv import load_dotenv

# Load .env from project root if it exists
ROOT_DIR = Path(__file__).resolve().parent.parent
env_path = ROOT_DIR / ".env"
if env_path.exists():
    load_dotenv(env_path)
else:
    print(f"⚠️ Warning: .env not found at {env_path}")

# DATABASE_URL is required to connect to Postgres
PG_DSN = os.environ.get("DATABASE_URL")

DEFAULT_FOUNDRY_MAINNET_FORK_SKILL = {
    "name": "foundry-poc-mainnet-fork",
    "description": "Guides Codex to build Foundry PoCs against pinned EVM mainnet-fork state when deployed addresses and RPC access are available.",
    "phases": ["poc", "report"],
    "frameworks": ["foundry"],
    "platforms": [],
    "gate": "foundry_mainnet_fork",
    "content": """# Foundry Mainnet-Fork PoC Guidance

Use this skill only for Foundry/EVM findings where a fork-based PoC is appropriate and the issue provides deployed contract addresses, chain context, and RPC access.

Required behavior:
- Use a pinned fork block with `vm.createSelectFork` or equivalent Foundry fork setup.
- Use real deployed protocol addresses and real token/account state.
- Do not use mocks, fake tokens, fake callbacks, `vm.store`, or manually corrupted storage to create the bug.
- Demonstrate the full causal exploit path with realistic attacker/victim/protocol actions.
- Assert concrete impact with balance deltas, unauthorized state changes, invariant breaks, or other accepted contest impact.
- If chain, deployed addresses, fork block, or RPC access are missing, do not force a fork PoC; report that normal local harness verification is required.
""",
}

def init_db():
    if not PG_DSN:
        print("❌ DATABASE_URL environment variable is not set.")
        print("Please ensure your .env file is loaded or the variable is exported.")
        return

    print("Connecting to PostgreSQL...")
    try:
        pg = psycopg2.connect(PG_DSN)
        pg.autocommit = True
    except Exception as e:
        print(f"❌ Could not connect to Postgres: {e}")
        return

    print("Initializing Postgres schema...")
    with pg.cursor() as cur:
        # 1. Create Tables
        cur.execute("""
            CREATE TABLE IF NOT EXISTS bots (
                id SERIAL PRIMARY KEY,
                name TEXT NOT NULL,
                platform TEXT NOT NULL,
                mode TEXT DEFAULT 'review',
                skills TEXT DEFAULT '[]',
                status TEXT DEFAULT 'idle',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
            
            CREATE TABLE IF NOT EXISTS contests (
                id SERIAL PRIMARY KEY,
                bot_id INTEGER REFERENCES bots(id),
                title TEXT NOT NULL,
                code_url TEXT,
                status TEXT DEFAULT 'active',
                earnings REAL DEFAULT 0,
                platform_contest_id TEXT UNIQUE,
                platform_url TEXT,
                description TEXT,
                prize_pool REAL DEFAULT 0,
                token TEXT DEFAULT 'USDC',
                logo_url TEXT DEFAULT '',
                synced_from TEXT DEFAULT 'manual',
                starts_at TIMESTAMP,
                ends_at TIMESTAMP,
                metadata JSONB DEFAULT '{}',
                is_finalized BOOLEAN DEFAULT FALSE,
                has_joined BOOLEAN DEFAULT FALSE,
                skip_autojoin BOOLEAN DEFAULT FALSE,
                last_synced_at TIMESTAMP
            );
            
            CREATE TABLE IF NOT EXISTS issues (
                id SERIAL PRIMARY KEY,
                contest_id INTEGER REFERENCES contests(id),
                platform_issue_id TEXT UNIQUE,
                title TEXT NOT NULL,
                description TEXT,
                severity TEXT,
                classification TEXT,
                confidence TEXT,
                status TEXT DEFAULT 'pending',
                linked_issue_id INTEGER REFERENCES issues(id)
            );
            
            CREATE TABLE IF NOT EXISTS skills (
                id SERIAL PRIMARY KEY,
                name TEXT UNIQUE NOT NULL,
                description TEXT NOT NULL,
                content TEXT NOT NULL,
                phases JSONB DEFAULT '["triage", "poc", "report"]'::jsonb,
                frameworks JSONB DEFAULT '[]'::jsonb,
                platforms JSONB DEFAULT '[]'::jsonb,
                gate TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
            
            CREATE TABLE IF NOT EXISTS notifications (
                id SERIAL PRIMARY KEY,
                contest_id INTEGER REFERENCES contests(id),
                issue_id INTEGER REFERENCES issues(id),
                message TEXT NOT NULL,
                is_read BOOLEAN DEFAULT FALSE,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
            
            CREATE TABLE IF NOT EXISTS platform_sync_log (
                id SERIAL PRIMARY KEY,
                platform TEXT NOT NULL,
                synced_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                contest_count INTEGER DEFAULT 0,
                new_count INTEGER DEFAULT 0,
                status TEXT DEFAULT 'success',
                error_message TEXT
            );

            CREATE TABLE IF NOT EXISTS trufy_scans (
                id SERIAL PRIMARY KEY,
                contest_id INTEGER REFERENCES contests(id) UNIQUE,
                trufy_project_id TEXT NOT NULL,
                status TEXT DEFAULT 'pending',     -- status of the scan
                results JSONB DEFAULT '{}',        -- the findings from Trufy
                triggered_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                result_fetched_at TIMESTAMP,
                findings_count INTEGER DEFAULT 0
            );

            CREATE TABLE IF NOT EXISTS verification_jobs (
                id TEXT PRIMARY KEY,              -- UUID string
                issue_ids JSONB NOT NULL,          -- e.g. [1, 2, 3]
                status TEXT DEFAULT 'pending',     -- pending | running | done | failed
                results JSONB DEFAULT '{}',        -- {issue_id: {status, poc_code, error}}
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
        """)

        # 2. Schema Evolution: Ensure critical columns exist (Idempotent ALTERS)
        # This fixes databases that were initialized with older versions of the script.
        print("Checking for missing columns...")
        cur.execute("ALTER TABLE contests ADD COLUMN IF NOT EXISTS has_joined BOOLEAN DEFAULT FALSE;")
        cur.execute("ALTER TABLE contests ADD COLUMN IF NOT EXISTS skip_autojoin BOOLEAN DEFAULT FALSE;")
        cur.execute("ALTER TABLE contests ADD COLUMN IF NOT EXISTS last_synced_at TIMESTAMP;")
        cur.execute("ALTER TABLE contests ADD COLUMN IF NOT EXISTS metadata JSONB DEFAULT '{}';")
        cur.execute("UPDATE contests SET metadata = '{}'::jsonb WHERE metadata IS NULL;")
        cur.execute("ALTER TABLE skills ADD COLUMN IF NOT EXISTS phases JSONB DEFAULT '[\"triage\", \"poc\", \"report\"]'::jsonb;")
        cur.execute("ALTER TABLE skills ADD COLUMN IF NOT EXISTS frameworks JSONB DEFAULT '[]'::jsonb;")
        cur.execute("ALTER TABLE skills ADD COLUMN IF NOT EXISTS platforms JSONB DEFAULT '[]'::jsonb;")
        cur.execute("ALTER TABLE skills ADD COLUMN IF NOT EXISTS gate TEXT;")
        cur.execute("UPDATE skills SET phases = '[\"triage\", \"poc\", \"report\"]'::jsonb WHERE phases IS NULL;")
        cur.execute("UPDATE skills SET frameworks = '[]'::jsonb WHERE frameworks IS NULL;")
        cur.execute("UPDATE skills SET platforms = '[]'::jsonb WHERE platforms IS NULL;")
        # Trufy scan tracking evolution
        cur.execute("ALTER TABLE trufy_scans ADD COLUMN IF NOT EXISTS status TEXT DEFAULT 'pending';")
        cur.execute("ALTER TABLE trufy_scans ADD COLUMN IF NOT EXISTS results JSONB DEFAULT '{}';")
        cur.execute("""
            DO $$
            BEGIN
                IF EXISTS (
                    SELECT 1
                    FROM information_schema.columns
                    WHERE table_name = 'trufy_scans'
                      AND column_name = 'scan_status'
                ) THEN
                    UPDATE trufy_scans
                    SET status = scan_status
                    WHERE status = 'pending'
                      AND scan_status IS NOT NULL;
                END IF;
            END $$;
        """)
        # Batch verification job tracking
        cur.execute("""
            CREATE TABLE IF NOT EXISTS verification_jobs (
                id TEXT PRIMARY KEY,
                issue_ids JSONB NOT NULL,
                status TEXT DEFAULT 'pending',
                results JSONB DEFAULT '{}',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
        """)

        # 3. Seed Default Bots
        print("Seeding default bots...")
        default_bots = [
            ("Guardian Scout", "guardian"),
            ("Sherlock Sentinel", "sherlock"),
            ("Code4rena Warden", "code4rena"),
            ("Immunefi Hunter", "immunefi")
        ]
        # 2. Schema Updates (Idempotent ALTER TABLE)
        print("Checking for missing columns...")
        
        # Add trufy_project_id to contests
        cur.execute("SELECT column_name FROM information_schema.columns WHERE table_name='contests' AND column_name='trufy_project_id'")
        if not cur.fetchone():
            print("   + Adding trufy_project_id to contests table...")
            cur.execute("ALTER TABLE contests ADD COLUMN trufy_project_id TEXT")
            
        # Add skip_autojoin to contests
        cur.execute("SELECT column_name FROM information_schema.columns WHERE table_name='contests' AND column_name='skip_autojoin'")
        if not cur.fetchone():
            print("   + Adding skip_autojoin to contests table...")
            cur.execute("ALTER TABLE contests ADD COLUMN skip_autojoin BOOLEAN DEFAULT FALSE")

        # Add poc_code, submission_report, and confidence to issues
        cur.execute("SELECT column_name FROM information_schema.columns WHERE table_name='issues' AND column_name='poc_code'")
        if not cur.fetchone():
            print("   + Adding poc_code to issues table...")
            cur.execute("ALTER TABLE issues ADD COLUMN poc_code TEXT")
            
        cur.execute("SELECT column_name FROM information_schema.columns WHERE table_name='issues' AND column_name='submission_report'")
        if not cur.fetchone():
            print("   + Adding submission_report to issues table...")
            cur.execute("ALTER TABLE issues ADD COLUMN submission_report TEXT")      
            
        cur.execute("SELECT column_name FROM information_schema.columns WHERE table_name='issues' AND column_name='confidence'")
        if not cur.fetchone():
            print("   + Adding confidence to issues table...")
            cur.execute("ALTER TABLE issues ADD COLUMN confidence TEXT")

        cur.execute("SELECT column_name FROM information_schema.columns WHERE table_name='issues' AND column_name='judge_status'")
        if not cur.fetchone():
            print("   + Adding judge_status to issues table...")
            cur.execute("ALTER TABLE issues ADD COLUMN judge_status TEXT")

        cur.execute("SELECT column_name FROM information_schema.columns WHERE table_name='issues' AND column_name='judge_verdict'")
        if not cur.fetchone():
            print("   + Adding judge_verdict to issues table...")
            cur.execute("ALTER TABLE issues ADD COLUMN judge_verdict TEXT DEFAULT 'Waiting for response'")
            
        cur.execute("SELECT column_name FROM information_schema.columns WHERE table_name='issues' AND column_name='linked_issue_id'")
        if not cur.fetchone():
            print("   + Adding linked_issue_id to issues table...")
            cur.execute("ALTER TABLE issues ADD COLUMN linked_issue_id INTEGER REFERENCES issues(id)")
            
        for name, platform in default_bots:
            # Check for existing bot with the same platform (case-insensitive)
            cur.execute("SELECT id FROM bots WHERE LOWER(platform) = %s", (platform.lower(),))
            if not cur.fetchone():
                print(f"   + Adding {name} bot...")
                cur.execute("INSERT INTO bots (name, platform) VALUES (%s, %s)", (name, platform.lower()))

        print("Seeding default skills...")
        cur.execute("""
            INSERT INTO skills (name, description, content, phases, frameworks, platforms, gate)
            VALUES (%s, %s, %s, %s::jsonb, %s::jsonb, %s::jsonb, %s)
            ON CONFLICT (name) DO UPDATE SET
                description = EXCLUDED.description,
                phases = EXCLUDED.phases,
                frameworks = EXCLUDED.frameworks,
                platforms = EXCLUDED.platforms,
                gate = EXCLUDED.gate
        """, (
            DEFAULT_FOUNDRY_MAINNET_FORK_SKILL["name"],
            DEFAULT_FOUNDRY_MAINNET_FORK_SKILL["description"],
            DEFAULT_FOUNDRY_MAINNET_FORK_SKILL["content"],
            json.dumps(DEFAULT_FOUNDRY_MAINNET_FORK_SKILL["phases"]),
            json.dumps(DEFAULT_FOUNDRY_MAINNET_FORK_SKILL["frameworks"]),
            json.dumps(DEFAULT_FOUNDRY_MAINNET_FORK_SKILL["platforms"]),
            DEFAULT_FOUNDRY_MAINNET_FORK_SKILL["gate"],
        ))

        # 4. Final Check / Log
        cur.execute("SELECT COUNT(*) FROM bots;")
        bot_count = cur.fetchone()[0]
        cur.execute("SELECT COUNT(*) FROM contests;")
        contest_count = cur.fetchone()[0]
        
        print("\n✅ Initialization complete.")
        print(f"   - Total Bots: {bot_count}")
        print(f"   - Total Contests: {contest_count}")
        print("Your data remains untouched.")

    pg.close()

if __name__ == "__main__":
    init_db()
