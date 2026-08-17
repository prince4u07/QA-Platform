"""
Create the QA Platform database and all of its tables.

Reads backend/schema.sql so that file stays the single source of truth for
the schema. Safe to run more than once: every statement uses IF NOT EXISTS,
so existing tables and their data are left untouched.

    python init_db.py

To change the schema, edit schema.sql. To add a column to a database that
already has data, use migrate_db.py instead.
"""

import os
import re
import sys

import MySQLdb

from config import Config

SCHEMA_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'schema.sql')

# schema.sql creates and selects the database by its literal name. init_db.py
# does that itself using Config, so the database name can be overridden through
# the DB_NAME environment variable. These statements are skipped from the file.
SKIP_PREFIXES = ('CREATE DATABASE', 'USE ')


def read_schema_statements(path):
    """Return the CREATE TABLE statements from schema.sql, comments stripped."""
    with open(path, 'r', encoding='utf-8') as f:
        sql = f.read()

    # Drop full-line and trailing "--" comments before splitting on ";",
    # so a semicolon can never be picked up from inside a comment.
    sql = re.sub(r'--[^\n]*', '', sql)

    statements = []
    for raw in sql.split(';'):
        stmt = raw.strip()
        if not stmt:
            continue
        if stmt.upper().startswith(SKIP_PREFIXES):
            continue
        statements.append(stmt)
    return statements


def table_name_of(statement):
    """Pull the table name out of a CREATE TABLE statement, for logging."""
    match = re.search(r'CREATE TABLE IF NOT EXISTS\s+(\w+)', statement, re.IGNORECASE)
    return match.group(1) if match else '(unknown)'


def main():
    if not os.path.exists(SCHEMA_FILE):
        print(f"x schema.sql not found at {SCHEMA_FILE}")
        return 1

    statements = read_schema_statements(SCHEMA_FILE)
    if not statements:
        print("x schema.sql contained no statements")
        return 1

    # Connect to the server without selecting a database, so the database
    # itself can be created if this is a fresh install.
    conn = MySQLdb.connect(
        host=Config.MYSQL_HOST,
        user=Config.MYSQL_USER,
        passwd=Config.MYSQL_PASSWORD,
    )
    cursor = conn.cursor()

    cursor.execute(
        f"CREATE DATABASE IF NOT EXISTS {Config.MYSQL_DB} "
        f"CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci"
    )
    print(f"- database '{Config.MYSQL_DB}' ready")
    cursor.execute(f"USE {Config.MYSQL_DB}")

    failed = 0
    for stmt in statements:
        name = table_name_of(stmt)
        try:
            cursor.execute(stmt)
            print(f"- table '{name}' ready")
        except Exception as e:
            failed += 1
            print(f"x table '{name}' failed: {e}")

    conn.commit()
    cursor.close()
    conn.close()

    if failed:
        print(f"\nx finished with {failed} failed statement(s)")
        return 1

    print(f"\n- done, {len(statements)} tables ready")
    return 0


if __name__ == '__main__':
    sys.exit(main())
