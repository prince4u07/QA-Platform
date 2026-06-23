"""
Shared Flask extension instances.

Kept in their own module so blueprints can import them without creating a
circular import with app.py.
"""

from flask_limiter import Limiter
from flask_limiter.util import get_remote_address

# Rate limiter. Default storage is in-memory (fine for a single dev process).
# For multi-process / production, set storage_uri to a Redis URL.
# No global default_limits: we opt-in per sensitive endpoint via @limiter.limit(...).
limiter = Limiter(
    key_func=get_remote_address,
    storage_uri="memory://",
    headers_enabled=True,
)
