
import os
from pathlib import Path

from dotenv import load_dotenv
from sqlalchemy import create_engine, text
from sqlalchemy.engine import URL

BASE_DIR = Path(__file__).resolve().parent

# Load ClinicQ database configuration.
load_dotenv(BASE_DIR / ".env", override=True)

host = os.getenv("DB_HOST", "127.0.0.1")
port = int(os.getenv("DB_PORT", "5432"))
database = os.getenv("DB_NAME")
username = os.getenv("DB_USER")
password = os.getenv("DB_PASSWORD")

print("ClinicQ Database Diagnostics")
print("-----------------------------")
print("Host:", host)
print("Port:", port)
print("Database:", database)
print("Username:", username)
print("Password configured:", bool(password))

if not all([database, username, password]):
    raise SystemExit("ERROR: Missing database configuration in .env")

url = URL.create(
    drivername="postgresql+psycopg",
    username=username,
    password=password,
    host=host,
    port=port,
    database=database,
)

engine = create_engine(
    url,
    connect_args={"connect_timeout": 5},
    pool_pre_ping=True,
)

try:
    with engine.connect() as connection:
        result = connection.execute(text("SELECT 1"))
        print("\nConnection result:", result.scalar_one())

        print("\nSUCCESS: Python connected to PostgreSQL.")

except Exception as exc:
    original_error = getattr(exc, "orig", exc)
    print("\nDATABASE CONNECTION FAILED")
    print("Error type:", type(exc).__name__)
    print("Details:", str(original_error))

finally:
    engine.dispose()
