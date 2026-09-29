import os
import psycopg2
from psycopg2.pool import SimpleConnectionPool
from psycopg2.extras import RealDictCursor
import redis

DATABASE_URL = os.environ.get("DATABASE_URL")
REDIS_URL = os.environ.get("REDIS_URL")

if not DATABASE_URL:
    raise RuntimeError("DATABASE_URL environment variable is not set")
if not REDIS_URL:
    raise RuntimeError("REDIS_URL environment variable is not set")

# Postgres connection pool
try:
    pg_pool = SimpleConnectionPool(
        1, 10,
        DATABASE_URL,
        cursor_factory=RealDictCursor
    )
except Exception as e:
    print("Database connection failed:", e)
    pg_pool = None

# Redis client
redis_client = redis.Redis.from_url(REDIS_URL, decode_responses=True)

def get_db():
    if pg_pool is None:
        raise RuntimeError("Database connection pool is not initialized. Check logs for connection errors.")
    conn = pg_pool.getconn()
    try:
        yield conn
    finally:
        pg_pool.putconn(conn)
