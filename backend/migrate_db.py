"""
Bring an existing QA Platform database up to date with schema.sql.

Run this when the database already holds data and schema.sql has gained a
new table or column:

    python migrate_db.py

What it does, in order:
  1. Creates any table in schema.sql that is missing.
  2. Adds any column that is missing from a table that already exists.
  3. Warns about leftover columns that would block inserts.

It only ever creates and adds. It never drops a table, drops a column,
renames anything, or deletes a row, so it is safe to run repeatedly and
safe to run against a database with real data in it.

Step 3 needs explaining. If a table was originally created from a
different schema, it can carry a column that is NOT NULL, has no default,
and is not written by the application. Every INSERT then fails. This
script cannot fix that safely on its own, because dropping the column
would destroy whatever is in it, so it reports the column and the two
ways to resolve it instead.
"""

import sys

import MySQLdb

from config import Config
from init_db import SCHEMA_FILE, read_schema_statements, table_name_of

# Definition lines inside CREATE TABLE that declare a constraint or index
# rather than a column.
NON_COLUMN_KEYWORDS = (
    'PRIMARY', 'FOREIGN', 'UNIQUE', 'INDEX', 'KEY', 'CONSTRAINT', 'CHECK', 'FULLTEXT',
)


def split_definitions(body):
    """Split a CREATE TABLE body on top-level commas, ignoring commas in types."""
    parts, depth, current = [], 0, []
    for ch in body:
        if ch == '(':
            depth += 1
        elif ch == ')':
            depth -= 1
        if ch == ',' and depth == 0:
            parts.append(''.join(current))
            current = []
        else:
            current.append(ch)
    parts.append(''.join(current))
    return [p.strip() for p in parts if p.strip()]


def parse_columns(statement):
    """
    Return {column_name: full definition} for one CREATE TABLE statement.

    The definition is returned verbatim so it can be handed straight to
    ALTER TABLE ... ADD COLUMN.
    """
    open_paren = statement.find('(')
    close_paren = statement.rfind(')')
    if open_paren == -1 or close_paren <= open_paren:
        return {}

    columns = {}
    for definition in split_definitions(statement[open_paren + 1:close_paren]):
        first_word = definition.split()[0].upper().strip('`')
        if first_word in NON_COLUMN_KEYWORDS:
            continue
        name = definition.split()[0].strip('`')
        # A column added later cannot carry the table's PRIMARY KEY clause.
        columns[name] = definition
    return columns


def live_columns(cursor, table):
    """Return {column_name: (is_nullable, default)} for a table that exists."""
    cursor.execute(
        """SELECT COLUMN_NAME, IS_NULLABLE, COLUMN_DEFAULT, EXTRA
           FROM information_schema.COLUMNS
           WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = %s""",
        (table,)
    )
    return {row[0]: (row[1], row[2], row[3]) for row in cursor.fetchall()}


def main():
    statements = read_schema_statements(SCHEMA_FILE)
    if not statements:
        print("x schema.sql contained no statements")
        return 1

    conn = MySQLdb.connect(
        host=Config.MYSQL_HOST,
        user=Config.MYSQL_USER,
        passwd=Config.MYSQL_PASSWORD,
        db=Config.MYSQL_DB,
    )
    cursor = conn.cursor()

    created_tables, added_columns, blockers = [], [], []

    for statement in statements:
        table = table_name_of(statement)
        expected = parse_columns(statement)

        existing = live_columns(cursor, table)
        if not existing:
            cursor.execute(statement)
            created_tables.append(table)
            print(f"- created table '{table}'")
            continue

        for name, definition in expected.items():
            if name in existing:
                continue
            # AUTO_INCREMENT and PRIMARY KEY belong to the original CREATE and
            # cannot be bolted on here; such a column means the table is too
            # far gone for an additive migration.
            upper = definition.upper()
            if 'AUTO_INCREMENT' in upper or 'PRIMARY KEY' in upper:
                blockers.append(
                    f"{table}.{name} is the primary key and is missing. "
                    f"This table cannot be migrated by adding columns."
                )
                continue
            try:
                cursor.execute(f"ALTER TABLE `{table}` ADD COLUMN {definition}")
                added_columns.append(f"{table}.{name}")
                print(f"- added column '{table}.{name}'")
            except Exception as e:
                blockers.append(f"{table}.{name} could not be added: {e}")
                print(f"x could not add '{table}.{name}': {e}")

        # Leftover NOT NULL columns with no default are not in schema.sql, so
        # nothing in the application ever supplies a value for them. Every
        # INSERT into this table will fail until they are dealt with.
        for name, (nullable, default, extra) in existing.items():
            if name in expected:
                continue
            if nullable == 'NO' and default is None and 'auto_increment' not in extra:
                blockers.append(
                    f"{table}.{name} is left over from an older schema, is NOT NULL "
                    f"with no default, and nothing in the app writes to it. "
                    f"Inserts into '{table}' will fail until you either drop this "
                    f"column or make it nullable."
                )

    conn.commit()
    cursor.close()
    conn.close()

    print("\n" + "=" * 60)
    print(f"tables created : {len(created_tables)}")
    print(f"columns added  : {len(added_columns)}")

    if blockers:
        print(f"\nNEEDS YOUR ATTENTION ({len(blockers)}):")
        for item in blockers:
            print(f"  ! {item}")
        print(
            "\nThese are not fixed automatically because the fix would destroy "
            "data.\nIf the table is empty, the simplest fix is to drop it and "
            "run init_db.py.\nIf it has data you need, make the column nullable:"
            "\n  ALTER TABLE <table> MODIFY <column> <type> NULL;"
        )
        return 1

    print("\n- database is up to date with schema.sql")
    return 0


if __name__ == '__main__':
    sys.exit(main())
