"""WordPress job expiration workflow."""

from __future__ import annotations

from datetime import datetime, timezone
import json
import logging
from pathlib import Path
import re
from time import sleep
from typing import Any

import polars as pl
import requests
from bs4 import BeautifulSoup
from requests.auth import HTTPBasicAuth

from .config import GeminiSettings, WordPressSettings
from .io import notify_google_indexing, write_dataframe


logger = logging.getLogger(__name__)

DEFAULT_READABLE_TEXT_MAX_CHARS = 6000
DEFAULT_EXPIRATION_CHECK_LIMIT = 20
SOFT_404_CHECK_RESPONSE_CODES = {200, 301, 302, 303, 307, 308}
SOFT_404_PHRASES = (
    "job not found",
    "position has been filled",
    "no longer accepting applications",
    "job expired",
    "page not found",
    "this posting is no longer available",
    "no longer available",
    "posting is no longer available",
    "this job is no longer available",
    "not accepting applications",
    "position has closed",
    "this vacancy has expired",
    "position is no longer available",
)
ACTIVE_JOB_PHRASES = (
    "apply now",
    "job description",
    "responsibilities",
    "qualifications",
    "about the role",
    "requirements",
    "preferred qualifications",
    "minimum qualifications",
    "benefits",
)

SOFT_404_SYSTEM_PROMPT = """
You are classifying job posting pages.

You will receive an array of page records. For each page, estimate the probability from 0 to 1
that the page is a soft-404, expired posting, not-found page, or otherwise no longer accepting applications.

Return STRICT JSON only.

Instructions:
- Base your judgment only on the provided record fields.
- Do not add markdown, code fences, or explanations outside JSON.
- Return an array of objects in the same order as the input indices.
- Each object must contain:
  - "index": integer
  - "soft_404_probability": float between 0 and 1
  - "reason": short string
- Do not treat ordinary corporate boilerplate, navigation, benefits text, or generic careers-site chrome as evidence of expiration.
- Strong evidence of expiration includes phrases like:
  - "job not found"
  - "position has been filled"
  - "no longer accepting applications"
  - "job expired"
  - "page not found"
  - "this posting is no longer available"
- Active job evidence includes detailed job descriptions, responsibilities, qualifications, and normal application language.

Output:
- Return JSON only, shaped like:
[
  {
    "index": 0,
    "soft_404_probability": 0.0,
    "reason": "Page appears to show an active job posting."
  }
]
""".strip()

EXPIRATION_HISTORY_COLUMNS = {
    "post_id",
    "title",
    "footnote",
    "checked_status",
    "checked_at",
    "response_code",
    "request_error",
    "prob_soft_404",
    "is_invalid",
    "is_valid",
    "was_privatized",
    "private_status_code",
    "private_error",
}


def fetch_wordpress_posts(
    settings: WordPressSettings,
    *,
    status: str = "draft",
    per_page: int = 100,
) -> list[dict[str, Any]]:
    response = requests.get(
        settings.posts_endpoint,
        params={"per_page": per_page, "page": 1, "status": status},
        auth=HTTPBasicAuth(settings.username, settings.app_password),
        timeout=30,
    )
    response.raise_for_status()
    return response.json()


def _read_dataframe(path: str | Path) -> pl.DataFrame:
    file_path = Path(path)
    if not file_path.exists():
        return pl.DataFrame()

    suffix = file_path.suffix.lower()
    if suffix == ".parquet":
        return pl.read_parquet(file_path)
    if suffix == ".csv":
        return pl.read_csv(file_path, try_parse_dates=True)
    if suffix == ".json":
        return pl.read_json(file_path)

    raise ValueError("Unsupported history format. Use a .parquet, .csv, or .json file.")


def _history_schema_dataframe() -> pl.DataFrame:
    return pl.DataFrame(
        schema={
            "post_id": pl.Int64,
            "title": pl.Utf8,
            "footnote": pl.Utf8,
            "checked_status": pl.Utf8,
            "checked_at": pl.Utf8,
            "response_code": pl.Int64,
            "request_error": pl.Utf8,
            "prob_soft_404": pl.Float64,
            "is_invalid": pl.Int8,
            "is_valid": pl.Int8,
            "was_privatized": pl.Int8,
            "private_status_code": pl.Int64,
            "private_error": pl.Utf8,
        }
    )


def load_expiration_history(path: str | Path | None) -> pl.DataFrame:
    if not path:
        return _history_schema_dataframe()

    history = _read_dataframe(path)
    if history.is_empty():
        return _history_schema_dataframe()

    for column in _history_schema_dataframe().columns:
        if column not in history.columns:
            history = history.with_columns(pl.lit(None).alias(column))

    return history.select(_history_schema_dataframe().columns)


def select_posts_for_expiration_check(
    posts_df: pl.DataFrame,
    history_df: pl.DataFrame,
    *,
    max_posts_to_check: int = DEFAULT_EXPIRATION_CHECK_LIMIT,
) -> pl.DataFrame:
    if posts_df.is_empty():
        return posts_df

    if history_df.is_empty():
        history_lookup = _history_schema_dataframe().select(["post_id", "checked_at", "was_privatized"])
    else:
        history_lookup = history_df.select(["post_id", "checked_at", "was_privatized"])

    eligible = posts_df.join(history_lookup, on="post_id", how="left")
    eligible = eligible.filter(
        pl.col("was_privatized").is_null() | (pl.col("was_privatized") != 1)
    )

    eligible = eligible.with_columns(
        pl.col("checked_at").fill_null("").alias("_checked_at_sort")
    )
    return eligible.sort("_checked_at_sort").head(max_posts_to_check).drop(
        [column for column in ("_checked_at_sort",) if column in eligible.columns]
    ).drop(
        [column for column in ("checked_at", "was_privatized") if column in eligible.columns]
    )


def _current_timestamp() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _same_output_target(first_path: str | None, second_path: str | None) -> bool:
    if not first_path or not second_path:
        return False

    return Path(first_path) == Path(second_path)


def _build_history_update_df(report: pl.DataFrame, *, checked_status: str) -> pl.DataFrame:
    if report.is_empty():
        return _history_schema_dataframe()

    columns = [
        "post_id",
        "title",
        "footnote",
        "checked_status",
        "checked_at",
        "response_code",
        "request_error",
        "prob_soft_404",
        "is_invalid",
        "is_valid",
        "was_privatized",
        "private_status_code",
        "private_error",
    ]
    working = report
    if "checked_status" not in working.columns:
        working = working.with_columns(pl.lit(checked_status).alias("checked_status"))
    if "was_privatized" not in working.columns:
        working = working.with_columns(pl.lit(0).cast(pl.Int8).alias("was_privatized"))
    for column in ("private_status_code", "private_error"):
        if column not in working.columns:
            working = working.with_columns(pl.lit(None).alias(column))

    return working.select(columns)


def update_expiration_history(
    history_df: pl.DataFrame,
    checked_report: pl.DataFrame,
    *,
    checked_status: str,
) -> pl.DataFrame:
    update_df = _build_history_update_df(checked_report, checked_status=checked_status)
    if history_df.is_empty():
        return update_df
    if update_df.is_empty():
        return history_df

    remaining_history = history_df.filter(~pl.col("post_id").is_in(update_df["post_id"]))
    return pl.concat([remaining_history, update_df], how="diagonal_relaxed").sort("checked_at", nulls_last=True)


def posts_to_dataframe(posts: list[dict[str, Any]]) -> pl.DataFrame:
    rows = []
    for post in posts:
        meta = post.get("meta") or {}
        footnote = meta.get("footnotes") or None
        title = post.get("title") or {}
        rows.append(
            {
                "post_id": post.get("id"),
                "title": title.get("rendered") if isinstance(title, dict) else None,
                "footnote": footnote,
            }
        )

    return (
        pl.DataFrame(rows)
        if rows
        else pl.DataFrame(schema={"post_id": pl.Int64, "title": pl.Utf8, "footnote": pl.Utf8})
    )


def fetch_footnote_metadata(df: pl.DataFrame) -> pl.DataFrame:
    response_codes: list[int | None] = []
    direct_url_html: list[str | None] = []
    request_errors: list[str | None] = []

    for footnote in df["footnote"].to_list():
        if not footnote:
            response_codes.append(None)
            direct_url_html.append(None)
            request_errors.append(None)
            continue

        try:
            response = requests.get(footnote, timeout=30)
            response_codes.append(response.status_code)
            direct_url_html.append(response.text)
            request_errors.append(None)
        except Exception as exc:
            response_codes.append(None)
            direct_url_html.append(None)
            request_errors.append(str(exc))

    return df.with_columns(
        pl.Series("response_code", response_codes),
        pl.Series("direct_url_html", direct_url_html),
        pl.Series("request_error", request_errors),
    )


def html_to_readable_text(html: str | None, *, max_chars: int = DEFAULT_READABLE_TEXT_MAX_CHARS) -> str:
    if not html:
        return ""

    soup = BeautifulSoup(html, "html.parser")

    for tag_name in ("script", "style", "noscript", "svg", "header", "nav", "footer"):
        for tag in soup.find_all(tag_name):
            tag.decompose()

    text = soup.get_text(separator=" ", strip=True)
    text = re.sub(r"\s+", " ", text).strip()
    return text[:max_chars]


def _legacy_pages_to_records(pages: list[str | None]) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for index, html in enumerate(pages):
        records.append(
            {
                "index": index,
                "title": None,
                "direct_url": None,
                "response_code": 200 if html else None,
                "readable_text": html_to_readable_text(html),
                "direct_url_html": html,
            }
        )
    return records


def _build_scoring_records(df: pl.DataFrame, *, max_text_chars: int = DEFAULT_READABLE_TEXT_MAX_CHARS) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for index, row in enumerate(df.iter_rows(named=True)):
        html = row.get("direct_url_html")
        records.append(
            {
                "index": index,
                "title": row.get("title"),
                "direct_url": row.get("footnote"),
                "response_code": row.get("response_code"),
                "readable_text": html_to_readable_text(html, max_chars=max_text_chars),
                "direct_url_html": html,
            }
        )
    return records


def _heuristic_soft_404_probability(record: dict[str, Any]) -> float | None:
    response_code = record.get("response_code")
    readable_text = str(record.get("readable_text") or "")
    lowered = readable_text.lower()

    if response_code is None:
        return 0.5

    if response_code == 404:
        return 1.0

    if response_code not in SOFT_404_CHECK_RESPONSE_CODES:
        return 1.0

    if not lowered:
        return 0.75

    if any(phrase in lowered for phrase in SOFT_404_PHRASES):
        return 0.98

    if any(phrase in lowered for phrase in ACTIVE_JOB_PHRASES):
        return 0.05

    return None


def _extract_json_array(text: str) -> str | None:
    stripped = text.strip()
    if stripped.startswith("[") and stripped.endswith("]"):
        return stripped

    match = re.search(r"\[\s*\{.*\}\s*\]", stripped, re.DOTALL)
    if match:
        return match.group(0)

    return None


def _parse_gemini_batch_response(response_text: str, expected_indices: list[int]) -> dict[int, float]:
    json_payload = _extract_json_array(response_text)
    if json_payload is None:
        raise ValueError("Gemini response did not contain a JSON array.")

    parsed = json.loads(json_payload)
    if not isinstance(parsed, list):
        raise ValueError("Gemini response was not a JSON list.")

    probabilities_by_index: dict[int, float] = {}
    for item in parsed:
        if not isinstance(item, dict):
            continue

        index = item.get("index")
        probability = item.get("soft_404_probability")
        if not isinstance(index, int):
            continue

        try:
            probability_float = float(probability)
        except (TypeError, ValueError):
            continue

        probabilities_by_index[index] = min(1.0, max(0.0, probability_float))

    for index in expected_indices:
        probabilities_by_index.setdefault(index, 0.5)

    return probabilities_by_index


def _gemini_batch_score_soft_404_probabilities(records: list[dict[str, Any]], settings: GeminiSettings) -> dict[int, float]:
    from google import genai
    from google.genai import types

    client = genai.Client(api_key=settings.api_key)
    payload = [
        {
            "index": record["index"],
            "job_title": record.get("title"),
            "direct_url": record.get("direct_url"),
            "http_status_code": record.get("response_code"),
            "readable_text": record.get("readable_text"),
        }
        for record in records
    ]
    total_input_size = sum(len(str(record.get("readable_text") or "")) for record in records)
    logger.info(
        "Sending %s pages to Gemini soft-404 scoring in one batch (approx %s chars).",
        len(records),
        total_input_size,
    )

    response = client.models.generate_content(
        model=settings.model,
        contents=json.dumps(payload, ensure_ascii=False),
        config=types.GenerateContentConfig(
            system_instruction=SOFT_404_SYSTEM_PROMPT,
            response_mime_type="application/json",
        ),
    )

    return _parse_gemini_batch_response(response.text, [record["index"] for record in records])


def score_soft_404_probabilities(
    pages_or_records: list[str | None] | list[dict[str, Any]],
    settings: GeminiSettings,
) -> list[float | None]:
    if not pages_or_records:
        return []

    first_item = pages_or_records[0]
    if isinstance(first_item, dict):
        records = [dict(record) for record in pages_or_records]  # shallow copy
        for record in records:
            record.setdefault("readable_text", html_to_readable_text(record.get("direct_url_html")))
            record.setdefault("index", len(records))
    else:
        records = _legacy_pages_to_records(pages_or_records)

    probabilities: list[float | None] = [None] * len(records)
    ambiguous_records: list[dict[str, Any]] = []

    for index, record in enumerate(records):
        record["index"] = index
        heuristic_probability = _heuristic_soft_404_probability(record)
        if heuristic_probability is None:
            ambiguous_records.append(record)
        else:
            probabilities[index] = heuristic_probability

    if not ambiguous_records:
        return probabilities

    try:
        scored_by_index = _gemini_batch_score_soft_404_probabilities(ambiguous_records, settings)
        for record in ambiguous_records:
            probabilities[record["index"]] = scored_by_index.get(record["index"], 0.5)
    except Exception as exc:
        message = str(exc)
        retry_match = re.search(r"retry.*?(\d+)", message, re.IGNORECASE)
        if retry_match:
            logger.warning("Gemini quota/rate issue while scoring soft-404s. Suggested retry delay: %s seconds.", retry_match.group(1))
        logger.warning("Gemini batch soft-404 scoring failed; using ambiguous fallback probabilities. Error: %s", exc)
        for record in ambiguous_records:
            probabilities[record["index"]] = 0.5

    return probabilities


def privatize_invalid_posts(
    posts: dict[int, str],
    settings: WordPressSettings,
    *,
    sleep_seconds: float = 0.2,
    google_credentials_file: str | None = None,
) -> dict[int, dict[str, Any]]:
    auth = HTTPBasicAuth(settings.username, settings.app_password)
    results: dict[int, dict[str, Any]] = {}

    for post_id, title in posts.items():
        updated_title = title if title.endswith(" [Expired]") else f"{title} [Expired]"
        response = requests.post(
            f"{settings.posts_endpoint}/{post_id}",
            json={"status": "private", "title": updated_title},
            auth=auth,
            timeout=30,
        )

        try:
            payload = response.json()
        except ValueError:
            payload = {}

        results[post_id] = {
            "private_status_code": response.status_code,
            "private_error": None if response.ok else payload.get("message", response.text[:200]),
        }
        if response.ok and google_credentials_file:
            post_url = payload.get("link", "")
            if post_url:
                notify_google_indexing(post_url, "URL_DELETED", google_credentials_file)
        sleep(sleep_seconds)

    return results


def run_expiration_check(
    *,
    wordpress_settings: WordPressSettings,
    gemini_settings: GeminiSettings,
    status: str = "draft",
    per_page: int = 100,
    threshold: float = 0.5,
    privatize_invalid: bool = True,
    sleep_seconds: float = 0.2,
    output_path: str | None = None,
    history_path: str | None = None,
    max_posts_to_check: int = DEFAULT_EXPIRATION_CHECK_LIMIT,
) -> pl.DataFrame:
    posts = fetch_wordpress_posts(wordpress_settings, status=status, per_page=per_page)
    all_posts = posts_to_dataframe(posts)
    history = load_expiration_history(history_path)
    same_output_and_history = _same_output_target(output_path, history_path)
    report = select_posts_for_expiration_check(
        all_posts,
        history,
        max_posts_to_check=max_posts_to_check,
    )

    if report.is_empty():
        if history_path:
            write_dataframe(history, history_path)
        if output_path and not same_output_and_history:
            write_dataframe(report, output_path)
        return report

    report = fetch_footnote_metadata(report)
    records = _build_scoring_records(report)
    probabilities = score_soft_404_probabilities(records, gemini_settings)
    report = report.with_columns(
        pl.Series("prob_soft_404", probabilities),
    )
    report = report.with_columns(
        (
            (~pl.col("response_code").is_in(sorted(SOFT_404_CHECK_RESPONSE_CODES)))
            | (pl.col("prob_soft_404") > threshold)
        ).cast(pl.Int8).alias("is_invalid"),
    )
    report = report.with_columns(
        (1 - pl.col("is_invalid")).cast(pl.Int8).alias("is_valid"),
        pl.lit(_current_timestamp()).alias("checked_at"),
        pl.lit(status).alias("checked_status"),
    )

    if privatize_invalid:
        invalid_rows = report.filter(pl.col("is_invalid") == 1).select(["post_id", "title"])
        invalid_posts = dict(zip(invalid_rows["post_id"].to_list(), invalid_rows["title"].to_list()))
        privatize_results = privatize_invalid_posts(
            invalid_posts,
            wordpress_settings,
            sleep_seconds=sleep_seconds,
        )
        report = report.with_columns(
            pl.Series(
                "private_status_code",
                [privatize_results.get(post_id, {}).get("private_status_code") for post_id in report["post_id"].to_list()],
            ),
            pl.Series(
                "private_error",
                [privatize_results.get(post_id, {}).get("private_error") for post_id in report["post_id"].to_list()],
            ),
        )
        report = report.with_columns(
            pl.when(
                (pl.col("is_invalid") == 1)
                & pl.col("private_status_code").is_not_null()
                & (pl.col("private_status_code") < 400)
            )
            .then(pl.lit(1))
            .otherwise(pl.lit(0))
            .cast(pl.Int8)
            .alias("was_privatized"),
        )
    else:
        report = report.with_columns(pl.lit(0).cast(pl.Int8).alias("was_privatized"))

    updated_history = None
    if history_path:
        updated_history = update_expiration_history(history, report, checked_status=status)
        write_dataframe(updated_history, history_path)
    if output_path and same_output_and_history:
        pass
    elif output_path:
        write_dataframe(report, output_path)

    return report
