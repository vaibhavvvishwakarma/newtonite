"""All configuration comes from environment variables (see .env.example)."""
import os

DATABASE_URL = os.getenv("DATABASE_URL", "postgresql://postgres:postgres@localhost:5432/opsdesk")
SECRET_KEY = os.getenv("SECRET_KEY", "dev-only-secret-change-me-in-production-please")
TOKEN_TTL_HOURS = int(os.getenv("TOKEN_TTL_HOURS", "12"))
BCRYPT_ROUNDS = int(os.getenv("BCRYPT_ROUNDS", "10"))
DB_POOL_MAX = int(os.getenv("DB_POOL_MAX", "20"))
SEED_ITEMS = int(os.getenv("SEED_ITEMS", "5000"))
