"""Input/output helpers for jcp-data-manager."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import polars as pl
from google.oauth2 import service_account
from googleapiclient.discovery import build


def _read_json(path: str | Path) -> Any:
    file_path = Path(path)
    if not file_path.exists():
        raise FileNotFoundError(f"JSON file not found: {file_path}")
    if not file_path.is_file():
        raise ValueError(f"Expected a file path, got: {file_path}")
    with file_path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


_INDEXING_SCOPES = ["https://www.googleapis.com/auth/indexing"]


def notify_google_indexing(url: str, notification_type: str, credentials_file: str) -> None:
    credentials = service_account.Credentials.from_service_account_file(
        credentials_file, scopes=_INDEXING_SCOPES
    )
    service = build("indexing", "v3", credentials=credentials)
    service.urlNotifications().publish(body={"url": url, "type": notification_type}).execute()


def load_sessions_data(path: str | Path) -> pl.DataFrame:
    payload = _read_json(path)
    if not isinstance(payload, dict) or "sessions" not in payload:
        raise ValueError("Session data must be a JSON object containing a 'sessions' key.")
    sessions = payload["sessions"]
    if not isinstance(sessions, list):
        raise ValueError("The 'sessions' value must be a JSON list.")
    return pl.DataFrame(sessions)


def write_dataframe(df: pl.DataFrame, path: str | Path) -> None:
    output_path = Path(path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    suffix = output_path.suffix.lower()
    if suffix == ".parquet":
        df.write_parquet(output_path)
        return
    if suffix == ".csv":
        df.write_csv(output_path)
        return
    if suffix == ".json":
        output_path.write_text(json.dumps(df.to_dicts(), ensure_ascii=False, indent=2), encoding="utf-8")
        return

    raise ValueError("Unsupported output format. Use a .parquet, .csv, or .json file.")
