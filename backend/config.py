import os
from dotenv import load_dotenv

load_dotenv()

class Config:
    # Database
    MYSQL_HOST = os.getenv('DB_HOST', 'localhost')
    MYSQL_USER = os.getenv('DB_USER', 'root')
    MYSQL_PASSWORD = os.getenv('DB_PASSWORD', '')
    MYSQL_DB = os.getenv('DB_NAME', 'qa_platform')
    MYSQL_CHARSET = 'utf8mb4'
    MYSQL_CURSORCLASS = 'DictCursor'

    # JWT
    JWT_SECRET_KEY = os.getenv('JWT_SECRET_KEY', 'fallback-secret')
    JWT_ACCESS_TOKEN_EXPIRES = False  # tokens don't expire (for dev)

    # The one account that gets admin rights, matched on the email used to
    # register. Roles are never accepted from a request body: that would let
    # anyone create themselves an admin through the public signup form.
    ADMIN_EMAIL = os.getenv('ADMIN_EMAIL', '').strip().lower()

    # File uploads
    UPLOAD_FOLDER = 'static/uploads'
    MAX_CONTENT_LENGTH = 200 * 1024 * 1024  # 200MB max file size



    # AI - Anthropic Claude
    ANTHROPIC_API_KEY = os.getenv('ANTHROPIC_API_KEY', '')
    AI_MODEL = 'claude-haiku-4-5-20251001'



    # Flask
    DEBUG = True