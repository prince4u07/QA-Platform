import MySQLdb
from config import Config

# Connect to MySQL server (not a specific database)
conn = MySQLdb.connect(
    host=Config.MYSQL_HOST,
    user=Config.MYSQL_USER,
    passwd=Config.MYSQL_PASSWORD
)

cursor = conn.cursor()

# Create database
try:
    cursor.execute(f"CREATE DATABASE IF NOT EXISTS {Config.MYSQL_DB} CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci")
    print(f"✓ Database '{Config.MYSQL_DB}' created/verified")
except Exception as e:
    print(f"✗ Error creating database: {e}")

# Select the database
cursor.execute(f"USE {Config.MYSQL_DB}")

# Create users table
cursor.execute("""
CREATE TABLE IF NOT EXISTS users (
    id INT AUTO_INCREMENT PRIMARY KEY,
    username VARCHAR(50) UNIQUE NOT NULL,
    email VARCHAR(100) UNIQUE NOT NULL,
    password VARCHAR(255) NOT NULL,
    role VARCHAR(20) DEFAULT 'tester',
    profile_picture VARCHAR(255),
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
)
""")
print("✓ Users table created/verified")

# Create projects table
cursor.execute("""
CREATE TABLE IF NOT EXISTS projects (
    id INT AUTO_INCREMENT PRIMARY KEY,
    user_id INT NOT NULL,
    name VARCHAR(100) NOT NULL,
    description TEXT,
    base_url VARCHAR(255),
    upload_path VARCHAR(255),
    environment VARCHAR(20) DEFAULT 'dev',
    source_type VARCHAR(20) DEFAULT 'url',
    has_active_session BOOLEAN DEFAULT FALSE,
    session_captured_at TIMESTAMP NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE,
    INDEX idx_user_id (user_id),
    INDEX idx_created_at (created_at)
)
""")
print("✓ Projects table created/verified")

# Create test_cases table
cursor.execute("""
CREATE TABLE IF NOT EXISTS test_cases (
    id INT AUTO_INCREMENT PRIMARY KEY,
    project_id INT NOT NULL,
    name VARCHAR(100) NOT NULL,
    steps TEXT,
    expected_result TEXT,
    priority VARCHAR(20),
    status VARCHAR(20) DEFAULT 'active',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (project_id) REFERENCES projects(id) ON DELETE CASCADE
)
""")
print("✓ Test cases table created/verified")

# Create test_runs table
cursor.execute("""
CREATE TABLE IF NOT EXISTS test_runs (
    id INT AUTO_INCREMENT PRIMARY KEY,
    test_case_id INT NOT NULL,
    status VARCHAR(20),
    screenshot VARCHAR(255),
    duration_ms INT,
    page_load_time_ms INT,
    console_errors INT,
    broken_links INT,
    missing_alt_images INT,
    issues_found INT,
    health_score INT,
    total_page_size_kb INT,
    run_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (test_case_id) REFERENCES test_cases(id) ON DELETE CASCADE
)
""")
print("✓ Test runs table created/verified")

# Create bugs table
cursor.execute("""
CREATE TABLE IF NOT EXISTS bugs (
    id INT AUTO_INCREMENT PRIMARY KEY,
    project_id INT,
    title VARCHAR(100) NOT NULL,
    description TEXT,
    severity VARCHAR(20),
    status VARCHAR(20) DEFAULT 'open',
    category VARCHAR(50),
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (project_id) REFERENCES projects(id) ON DELETE SET NULL
)
""")
print("✓ Bugs table created/verified")

conn.commit()
cursor.close()
conn.close()

print("\n✓ Database initialization complete!")
