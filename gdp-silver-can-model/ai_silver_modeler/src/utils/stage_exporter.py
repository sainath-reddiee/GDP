"""Utilities for uploading generated artifacts to a Snowflake stage."""

from __future__ import annotations

import os
import tempfile
import urllib.request
from datetime import datetime, timedelta, timezone
from io import BytesIO
from pathlib import Path
from typing import Any, Dict, Optional

from utils.connection import get_snowflake_connection


class StageExportError(Exception):
    """Raised when a stage export operation fails."""


def upload_bytes_to_stage(
    data: bytes,
    filename: str,
    stage_name: str,
    subdirectory: str = "",
    conn=None,
    expires_in: int = 3600
) -> Dict[str, Any]:
    """Upload a bytes payload to a Snowflake stage and return a presigned URL."""
    if not isinstance(data, (bytes, bytearray)) or len(data) == 0:
        raise StageExportError("Export payload must be non-empty bytes")

    if not filename:
        raise StageExportError("Filename is required for stage export")

    if not stage_name:
        raise StageExportError("Stage name is required for stage export")

    stage_ref = stage_name if stage_name.startswith("@") else f"@{stage_name}"
    remote_dir = subdirectory.strip("/ ")
    remote_filename = Path(filename).name.replace(" ", "_")
    remote_path = f"{remote_dir}/{remote_filename}" if remote_dir else remote_filename

    expires_in = max(expires_in, 60)

    close_conn = False

    def _ensure_connection():
        nonlocal conn, close_conn
        if conn is None:
            conn = get_snowflake_connection()
            close_conn = True
        return conn

    # Try Snowpark session if available
    snowpark_session = None
    try:
        from snowflake.snowpark.context import get_active_session
        snowpark_session = get_active_session()
    except Exception:
        snowpark_session = None

    def _presigned_url(operation: str) -> Optional[str]:
        escaped_path = remote_path.replace("'", "''")
        sql = f"SELECT GET_PRESIGNED_URL('{stage_ref}', '{escaped_path}', {expires_in}, '{operation}')"
        if snowpark_session is not None:
            rows = snowpark_session.sql(sql).collect()
            return rows[0][0] if rows else None
        _ensure_connection()
        cur = conn.cursor()
        try:
            cur.execute(sql)
            row = cur.fetchone()
            return row[0] if row else None
        finally:
            cur.close()

    # Prefer Snowpark session uploads to avoid implicit compression in presigned PUT flows
    if snowpark_session is not None:
        try:
            remote_uri = f"{stage_ref}/{remote_path}"
            snowpark_session.file.put_stream(
                BytesIO(data),
                remote_uri,
                auto_compress=False,
                overwrite=True
            )

            presigned_rows = snowpark_session.sql(
                f"SELECT GET_PRESIGNED_URL('{stage_ref}', '{remote_path}', {expires_in})"
            ).collect()
            presigned_url = presigned_rows[0][0] if presigned_rows else None
            if not presigned_url:
                raise StageExportError("Snowflake did not return a presigned URL")

            expires_at = (datetime.now(timezone.utc) + timedelta(seconds=expires_in)).isoformat()
            result = {
                "url": presigned_url,
                "stage": stage_ref,
                "stage_path": f"{stage_ref}/{remote_path}",
                "expires_at": expires_at
            }
            if close_conn and conn:
                conn.close()
            return result
        except Exception:
            pass

    # Primary path: presigned upload via HTTPS (works in stored procs / SiS)
    try:
        url_put = _presigned_url("PUT")
        if url_put:
            req = urllib.request.Request(url_put, data=data, method="PUT")
            req.add_header("Content-Type", "application/octet-stream")
            with urllib.request.urlopen(req, timeout=60) as resp:
                status = getattr(resp, "status", 200)
                if status not in (200, 201):
                    raise StageExportError(f"Presigned upload failed with status {status}")

            url_get = _presigned_url("GET")
            if not url_get:
                raise StageExportError("Snowflake did not return a presigned URL")

            expires_at = (datetime.now(timezone.utc) + timedelta(seconds=expires_in)).isoformat()
            result = {
                "url": url_get,
                "stage": stage_ref,
                "stage_path": f"{stage_ref}/{remote_path}",
                "expires_at": expires_at
            }
            if close_conn and conn:
                conn.close()
            return result
    except Exception:
        pass

    # Final fallback: local dev using PUT (not allowed in stored procs)
    cursor = None
    tmp_path: Optional[Path] = None

    try:
        _ensure_connection()
        tmp_file = tempfile.NamedTemporaryFile(delete=False)
        tmp_file.write(data)
        tmp_file.flush()
        tmp_file.close()
        tmp_path = Path(tmp_file.name)

        local_uri = str(tmp_path)
        if os.name == "nt":
            local_uri = local_uri.replace("\\", "/")
        put_sql = (
            f"PUT file://{local_uri} {stage_ref}/{remote_path} "
            "AUTO_COMPRESS=FALSE OVERWRITE=TRUE"
        )

        cursor = conn.cursor()
        cursor.execute(put_sql)

        presigned_sql = (
            f"SELECT GET_PRESIGNED_URL('{stage_ref}', '{remote_path}', {expires_in})"
        )
        cursor.execute(presigned_sql)
        row = cursor.fetchone()
        if not row or row[0] is None:
            raise StageExportError("Snowflake did not return a presigned URL")

        expires_at = (datetime.now(timezone.utc) + timedelta(seconds=expires_in)).isoformat()
        return {
            "url": row[0],
            "stage": stage_ref,
            "stage_path": f"{stage_ref}/{remote_path}",
            "expires_at": expires_at
        }
    finally:
        if cursor:
            cursor.close()
        if close_conn and conn:
            conn.close()
        if tmp_path and tmp_path.exists():
            tmp_path.unlink(missing_ok=True)
