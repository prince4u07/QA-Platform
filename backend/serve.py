"""Production WSGI entrypoint for local or Windows deployments."""

import os
from waitress import serve

from app import app


if __name__ == "__main__":
    serve(
        app,
        host=os.getenv("FLASK_HOST", "0.0.0.0"),
        port=int(os.getenv("FLASK_PORT", "5000")),
        threads=int(os.getenv("WEB_THREADS", "8")),
        connection_limit=int(os.getenv("WEB_CONNECTION_LIMIT", "500")),
        channel_timeout=int(os.getenv("WEB_CHANNEL_TIMEOUT", "120")),
    )
