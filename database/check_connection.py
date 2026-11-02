import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import psycopg

from config import Config


def main() -> int:
    try:
        with psycopg.connect(
            Config.DATABASE_URL,
            connect_timeout=10,
            sslmode="require",
        ) as connection:
            with connection.cursor() as cursor:
                cursor.execute("SELECT 1")
                result = cursor.fetchone()
        if result != (1,):
            print("FAIL: Connected to PostgreSQL, but the test query returned an unexpected result.")
            return 1
    except psycopg.Error as error:
        print(f"FAIL: Could not connect to the configured PostgreSQL database: {error}")
        return 1

    print("PASS: Connected to PostgreSQL and the test query succeeded.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
