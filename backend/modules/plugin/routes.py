import json
import logging

from flask import Blueprint, jsonify, request
from flask_jwt_extended import get_jwt_identity, jwt_required

logger = logging.getLogger(__name__)
plugin_bp = Blueprint("plugin", __name__)
mysql = None


def init_plugin(app_mysql):
    global mysql
    mysql = app_mysql


@plugin_bp.post("/analysis")
@jwt_required()
def create_analysis():
    if mysql is None:
        return jsonify({"error": "Database is unavailable"}), 503
    payload = request.get_json(silent=True)
    if not isinstance(payload, dict):
        return jsonify({"error": "A JSON analysis result is required"}), 400
    if payload.get("schemaVersion") not in {"1.0", "1.1"}:
        return jsonify({"error": "Unsupported analysis schema version"}), 400
    if not isinstance(payload.get("issues", []), list):
        return jsonify({"error": "issues must be an array"}), 400
    raw = json.dumps(payload, ensure_ascii=False)
    if len(raw) > 10_000_000:
        return jsonify({"error": "Analysis result is too large"}), 413
    cursor = mysql.connection.cursor()
    try:
        cursor.execute(
            """INSERT INTO plugin_analyses
               (user_id, analysis_id, workspace, target_url, status, payload)
               VALUES (%s, %s, %s, %s, %s, %s)""",
            (
                get_jwt_identity(),
                str(payload.get("analysisId", ""))[:100],
                str(payload.get("workspace", ""))[:500],
                str(payload.get("url", ""))[:500],
                str(payload.get("status", "completed"))[:30],
                raw,
            ),
        )
        mysql.connection.commit()
        analysis_id = cursor.lastrowid
    except Exception:
        mysql.connection.rollback()
        logger.exception("Failed to store plugin analysis")
        return jsonify({"error": "Could not store analysis result"}), 500
    finally:
        cursor.close()
    return jsonify({"id": analysis_id, "status": "accepted"}), 201


@plugin_bp.get("/analysis/<int:analysis_id>")
@jwt_required()
def get_analysis(analysis_id):
    if mysql is None:
        return jsonify({"error": "Database is unavailable"}), 503
    cursor = mysql.connection.cursor()
    cursor.execute(
        """SELECT id, analysis_id, workspace, target_url, status, payload, created_at
           FROM plugin_analyses WHERE id = %s AND user_id = %s""",
        (analysis_id, get_jwt_identity()),
    )
    row = cursor.fetchone()
    cursor.close()
    if row is None:
        return jsonify({"error": "Analysis not found"}), 404
    try:
        payload = json.loads(row["payload"])
    except (TypeError, ValueError):
        payload = {}
    return jsonify({
        "id": row["id"],
        "analysisId": row["analysis_id"],
        "workspace": row["workspace"],
        "url": row["target_url"],
        "status": row["status"],
        "result": payload,
        "createdAt": row["created_at"].isoformat() if row["created_at"] else None,
    })
