"""
Database migration script to update existing projects table schema
Run this after updating init_db.py to sync with the new schema
"""

import MySQLdb
from config import Config

try:
    # Connect to MySQL
    conn = MySQLdb.connect(
        host=Config.MYSQL_HOST,
        user=Config.MYSQL_USER,
        passwd=Config.MYSQL_PASSWORD,
        db=Config.MYSQL_DB
    )
    cursor = conn.cursor()

    # Add missing columns if they don't exist
    alter_queries = [
        "ALTER TABLE projects ADD COLUMN environment VARCHAR(20) DEFAULT 'dev' IF NOT EXISTS;",
        "ALTER TABLE projects ADD COLUMN source_type VARCHAR(20) DEFAULT 'url' IF NOT EXISTS;",
        "ALTER TABLE projects ADD COLUMN has_active_session BOOLEAN DEFAULT FALSE IF NOT EXISTS;",
        "ALTER TABLE projects ADD COLUMN session_captured_at TIMESTAMP NULL IF NOT EXISTS;",
    ]

    # Note: The above uses IF NOT EXISTS which is MySQL 8.0+
    # For older versions, we'll do it differently:
    
    # Check and add environment column
    cursor.execute("SHOW COLUMNS FROM projects LIKE 'environment'")
    if not cursor.fetchone():
        cursor.execute("ALTER TABLE projects ADD COLUMN environment VARCHAR(20) DEFAULT 'dev'")
        print("✓ Added 'environment' column")
    
    # Check and add source_type column
    cursor.execute("SHOW COLUMNS FROM projects LIKE 'source_type'")
    if not cursor.fetchone():
        cursor.execute("ALTER TABLE projects ADD COLUMN source_type VARCHAR(20) DEFAULT 'url'")
        print("✓ Added 'source_type' column")
    
    # Check and add has_active_session column
    cursor.execute("SHOW COLUMNS FROM projects LIKE 'has_active_session'")
    if not cursor.fetchone():
        cursor.execute("ALTER TABLE projects ADD COLUMN has_active_session BOOLEAN DEFAULT FALSE")
        print("✓ Added 'has_active_session' column")
    
    # Check and add session_captured_at column
    cursor.execute("SHOW COLUMNS FROM projects LIKE 'session_captured_at'")
    if not cursor.fetchone():
        cursor.execute("ALTER TABLE projects ADD COLUMN session_captured_at TIMESTAMP NULL")
        print("✓ Added 'session_captured_at' column")

    # Check and add requires_login column (marks login-gated sites so a no-session
    # automated run warns the user instead of silently crawling logged-out)
    cursor.execute("SHOW COLUMNS FROM projects LIKE 'requires_login'")
    if not cursor.fetchone():
        cursor.execute("ALTER TABLE projects ADD COLUMN requires_login BOOLEAN DEFAULT FALSE")
        print("✓ Added 'requires_login' column")
    
    # Add indexes for performance
    cursor.execute("SHOW INDEX FROM projects WHERE column_name='user_id'")
    if not cursor.fetchone():
        cursor.execute("ALTER TABLE projects ADD INDEX idx_user_id (user_id)")
        print("✓ Added index on 'user_id'")
    
    cursor.execute("SHOW INDEX FROM projects WHERE column_name='created_at'")
    if not cursor.fetchone():
        cursor.execute("ALTER TABLE projects ADD INDEX idx_created_at (created_at)")
        print("✓ Added index on 'created_at'")
    
    conn.commit()
    cursor.close()
    conn.close()
    
    print("\n✓ Database migration complete!")

except Exception as e:
    print(f"✗ Migration error: {e}")
    import traceback
    traceback.print_exc()
