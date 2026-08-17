-- ============================================================
-- QA Platform - Database Schema (MySQL 8.0)
--
-- This file describes the database exactly as the backend code
-- uses it. Every table, column name and type below was taken from
-- the SQL statements in backend/modules/*/routes.py, so the
-- application runs against this schema without modification.
--
-- 9 tables, mapped to the seven project modules:
--   users, login_history          -> Module 1  Authentication
--   projects, test_cases          -> Module 2  Project & Test Case Management
--   test_runs, crawled_pages      -> Module 3  Crawler & Test Runner
--   otp_sessions                  -> Module 5  Authenticated Crawling
--   ai_suggestions                -> Module 6  AI Assistant
--   bugs                          -> Module 7  Bug Tracker & Reports
--
-- Module 4 (Quality Analysis Engine) has no table of its own. Its six
-- check categories are stored as JSON text on test_runs and
-- crawled_pages, one column per category.
--
-- Module 5 stores the captured browser session as a file at
-- static/uploads/sessions/<project_id>.json, not as a table. The
-- projects table only records whether a session exists.
-- ============================================================

CREATE DATABASE IF NOT EXISTS qa_platform
    CHARACTER SET utf8mb4
    COLLATE utf8mb4_unicode_ci;

USE qa_platform;


-- ============================================================
-- MODULE 1 - AUTHENTICATION
-- ============================================================

-- Registered users. `password` holds a bcrypt hash, never plain text.
CREATE TABLE IF NOT EXISTS users (
    id              INT AUTO_INCREMENT PRIMARY KEY,
    username        VARCHAR(50)  NOT NULL UNIQUE,
    email           VARCHAR(100) NOT NULL UNIQUE,
    password        VARCHAR(255) NOT NULL,
    -- 'tester' or 'admin'. Never set from a request body: admin is granted
    -- only when the registration email matches ADMIN_EMAIL in the config.
    role            VARCHAR(20)  DEFAULT 'tester',
    -- A deactivated user keeps their data but cannot sign in or use their
    -- existing token. Preferred over deletion, which cascades irreversibly.
    is_active       BOOLEAN      DEFAULT TRUE,
    email_verified  BOOLEAN      DEFAULT FALSE,
    profile_picture VARCHAR(255),
    created_at      TIMESTAMP    DEFAULT CURRENT_TIMESTAMP,
    updated_at      TIMESTAMP    DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;


-- Login attempts, shown on the profile page as recent activity.
CREATE TABLE IF NOT EXISTS login_history (
    id           INT AUTO_INCREMENT PRIMARY KEY,
    user_id      INT NOT NULL,
    ip_address   VARCHAR(45),                -- 45 chars fits an IPv6 address
    user_agent   VARCHAR(255),
    success      BOOLEAN   DEFAULT TRUE,
    logged_in_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE,
    INDEX idx_login_history_user (user_id, logged_in_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;


-- ============================================================
-- MODULE 2 - PROJECT & TEST CASE MANAGEMENT
-- ============================================================

-- One row per target website under test. base_url is the live URL the
-- crawler starts from.
-- has_active_session / session_captured_at track whether a saved login
-- session file exists for this project (see Module 5).
CREATE TABLE IF NOT EXISTS projects (
    id                  INT AUTO_INCREMENT PRIMARY KEY,
    user_id             INT NOT NULL,
    name                VARCHAR(100) NOT NULL,
    description         TEXT,
    base_url            VARCHAR(255),
    environment         VARCHAR(20)  DEFAULT 'dev',    -- dev | staging | prod
    has_active_session  BOOLEAN      DEFAULT FALSE,
    session_captured_at TIMESTAMP    NULL,
    created_at          TIMESTAMP    DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE,
    INDEX idx_projects_user (user_id),
    INDEX idx_projects_created (created_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;


-- Test cases belonging to a project.
-- crawl_pages and max_pages configure the crawler: when crawl_pages is
-- true the runner follows links up to max_pages pages, otherwise it
-- tests the single base URL.
CREATE TABLE IF NOT EXISTS test_cases (
    id                   INT AUTO_INCREMENT PRIMARY KEY,
    project_id           INT NOT NULL,
    title                VARCHAR(255) NOT NULL,
    description          TEXT,
    steps                TEXT,
    expected_result      TEXT,
    priority             VARCHAR(20) DEFAULT 'Medium',   -- High | Medium | Low
    status               VARCHAR(20) DEFAULT 'Pending',  -- Pass | Fail | Pending
    test_type            VARCHAR(20) DEFAULT 'manual',   -- manual | automated
    automation_framework VARCHAR(50) DEFAULT 'none',
    crawl_pages          BOOLEAN     DEFAULT FALSE,
    max_pages            INT         DEFAULT 1,          -- clamped to 1..100 by the API
    created_at           TIMESTAMP   DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (project_id) REFERENCES projects(id) ON DELETE CASCADE,
    INDEX idx_test_cases_project (project_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;


-- ============================================================
-- MODULE 3 & 4 - TEST RUNNER AND QUALITY ANALYSIS RESULTS
-- ============================================================

-- One row per completed run of a test case.
-- The six check categories are stored as JSON text, one column each,
-- holding the full list of findings for that category across the run.
-- findings_evidence holds the merged, de-duplicated result set.
-- These are LONGTEXT rather than the JSON type so that a malformed or
-- partial payload can never make a run fail to save.
-- error_message is set only when the run itself crashed.
CREATE TABLE IF NOT EXISTS test_runs (
    id                   INT AUTO_INCREMENT PRIMARY KEY,
    test_case_id         INT NOT NULL,
    status               VARCHAR(20),          -- Pass | Fail
    screenshot           VARCHAR(500),
    run_at               TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    duration_ms          INT,
    page_load_time_ms    INT,
    issues_found         INT DEFAULT 0,
    health_score         INT DEFAULT 0,        -- 0..100
    total_page_size_kb   INT DEFAULT 0,
    total_requests       INT DEFAULT 0,        -- pages visited in this run
    -- the quality dimensions, each a JSON array of findings
    -- functional_issues holds steps from the test case that failed to execute,
    -- which is how a broken feature or workflow is detected.
    functional_issues    LONGTEXT,
    -- Step-by-step record of the written workflow: what ran, what passed,
    -- what failed and the screenshot taken at each step.
    steps_result         LONGTEXT,
    broken_links         LONGTEXT,
    console_errors       LONGTEXT,
    accessibility_issues LONGTEXT,
    seo_issues           LONGTEXT,
    security_issues      LONGTEXT,
    mobile_issues        LONGTEXT,
    missing_alt_images   LONGTEXT,
    performance_issues   LONGTEXT,
    findings_evidence    LONGTEXT,
    -- Per-category subscores behind health_score, so the number can be
    -- explained rather than just trusted.
    score_breakdown      LONGTEXT,
    -- What the audit did not cover: capped link checks, skipped mobile
    -- checks, accessibility fallback. Empty means the audit was complete.
    coverage             LONGTEXT,
    -- What changed since the previous run of this same test case:
    -- newly introduced issues, issues that are now fixed, score movement.
    regression           LONGTEXT,
    error_message        VARCHAR(500),
    FOREIGN KEY (test_case_id) REFERENCES test_cases(id) ON DELETE CASCADE,
    INDEX idx_test_runs_case (test_case_id, run_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;


-- One row per page visited during a run, with that page's own score,
-- screenshot and findings. This is what the per-page evidence view reads.
CREATE TABLE IF NOT EXISTS crawled_pages (
    id                INT AUTO_INCREMENT PRIMARY KEY,
    test_run_id       INT NOT NULL,
    url               VARCHAR(2000) NOT NULL,
    page_title        VARCHAR(500),
    status_code       INT,
    health_score      INT,
    issues_found      INT,
    page_load_time_ms INT,
    screenshot        VARCHAR(500),
    findings_evidence LONGTEXT,
    crawled_at        TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (test_run_id) REFERENCES test_runs(id) ON DELETE CASCADE,
    INDEX idx_crawled_pages_run (test_run_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci ROW_FORMAT=DYNAMIC;


-- ============================================================
-- MODULE 5 - AUTHENTICATED CRAWLING (OTP HANDSHAKE)
-- ============================================================

-- Coordinates one-time-password entry between the live browser and the
-- user. The runner creates a 'waiting' row, the user submits the code
-- through the API, and the runner picks it up and continues.
--
-- test_run_id deliberately has no foreign key: the OTP prompt appears
-- while the crawl is still running, before the test_runs row is written.
CREATE TABLE IF NOT EXISTS otp_sessions (
    id           INT AUTO_INCREMENT PRIMARY KEY,
    test_run_id  INT NOT NULL,
    status       VARCHAR(20) DEFAULT 'waiting',
                 -- waiting | submitted | completed | cancelled | timeout
    message      VARCHAR(500),
    otp_code     VARCHAR(20),
    created_at   TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    expires_at   TIMESTAMP NULL,
    submitted_at TIMESTAMP NULL,
    INDEX idx_otp_sessions_run (test_run_id, status)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;


-- ============================================================
-- MODULE 6 - AI ASSISTANT
-- ============================================================

-- Test cases proposed by the AI assistant, kept per project so they can
-- be reviewed before being promoted into real test_cases rows.
CREATE TABLE IF NOT EXISTS ai_suggestions (
    id          INT AUTO_INCREMENT PRIMARY KEY,
    project_id  INT NOT NULL,
    title       VARCHAR(255) NOT NULL,
    description TEXT,
    priority    VARCHAR(20) DEFAULT 'Medium',
    accepted    BOOLEAN     DEFAULT FALSE,
    created_at  TIMESTAMP   DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (project_id) REFERENCES projects(id) ON DELETE CASCADE,
    INDEX idx_ai_suggestions_project (project_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;


-- ============================================================
-- MODULE 7 - BUG TRACKER
-- ============================================================

-- Defects, either raised by hand or generated from a test run's findings.
-- A bug links to the test case it came from, and to the run that produced
-- it when it was auto-generated. Ownership is resolved by following
-- test_case_id -> project -> user, so there is no direct project_id.
-- evidence holds the original finding as JSON text.
CREATE TABLE IF NOT EXISTS bugs (
    id                 INT AUTO_INCREMENT PRIMARY KEY,
    test_case_id       INT,
    test_run_id        INT,
    title              VARCHAR(255) NOT NULL,
    description        TEXT,
    severity           VARCHAR(20) DEFAULT 'Major',  -- Critical | Major | Minor
    status             VARCHAR(20) DEFAULT 'Open',
                       -- Open | In Progress | Resolved | Closed
    category           VARCHAR(50),
                       -- broken-link | console-error | missing-alt | seo
                       -- | security | accessibility | mobile | other
    steps_to_reproduce TEXT,
    expected_behavior  TEXT,
    actual_behavior    TEXT,
    evidence           LONGTEXT,
    assigned_to        INT,
    created_at         TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    resolved_at        TIMESTAMP NULL,
    FOREIGN KEY (test_case_id) REFERENCES test_cases(id) ON DELETE SET NULL,
    FOREIGN KEY (test_run_id)  REFERENCES test_runs(id)  ON DELETE SET NULL,
    FOREIGN KEY (assigned_to)  REFERENCES users(id)      ON DELETE SET NULL,
    INDEX idx_bugs_test_case (test_case_id),
    INDEX idx_bugs_assigned (assigned_to),
    INDEX idx_bugs_status (status)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
