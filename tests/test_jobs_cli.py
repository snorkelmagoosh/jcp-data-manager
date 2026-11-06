from __future__ import annotations

from datetime import datetime
import json
from pathlib import Path
import polars as pl
import pytest

from jcp_data_manager.cli import build_parser
from jcp_data_manager.config import (
    GeminiSettings,
    WordPressSettings,
    load_environment,
    load_gemini_settings,
    load_github_models_settings,
    load_wordpress_settings,
)
from jcp_data_manager.expiration import run_expiration_check
from jcp_data_manager.expiration import (
    html_to_readable_text,
    score_soft_404_probabilities,
)
from jcp_data_manager.jobs import (
    build_wordpress_post_meta,
    build_similar_jobs_query,
    build_similar_jobs_survey_url,
    build_survey_url,
    build_similar_jobs_url,
    calculate_hours_old,
    default_jobs_output_path,
    extract_state_abbreviation,
    insert_navigation_urls_into_post_html,
    _normalize_similar_jobs_term,
)
from jcp_data_manager.job_templates import build_job_description_system_prompt, build_post_content


def test_build_post_content_respects_linkedin_toggle() -> None:
    with_linkedin = build_post_content(
        google_script="<script>google</script>",
        generated_html="<p>body</p>",
        include_linkedin_popup=True,
        experiment=1,
    )
    without_linkedin = build_post_content(
        google_script="<script>google</script>",
        generated_html="<p>body</p>",
        include_linkedin_popup=False,
        experiment=1,
    )

    assert "Sign in with LinkedIn" in with_linkedin
    assert "Sign in with LinkedIn" not in without_linkedin
    assert "randomizeTreat" in without_linkedin
    assert "fetchJcpSessionId" in with_linkedin
    assert "action: 'jcpst_get_session_id'" in with_linkedin
    assert 'console.error("Failed to save survey row:", status, error);' in with_linkedin
    assert "document.addEventListener(\"DOMContentLoaded\", randomizeTreat);" in with_linkedin
    assert '<div id="jcp-login-overlay">' in without_linkedin
    assert 'console.warn("Overlay elements missing");' in without_linkedin


def test_experiment_zero_has_no_treatment_prompt_or_randomization() -> None:
    prompt = build_job_description_system_prompt(0)
    post_content = build_post_content(
        google_script="<script>google</script>",
        generated_html="<p>body</p>",
        include_linkedin_popup=True,
        experiment=0,
    )

    assert "Treatment text" not in prompt
    assert "randomizeTreat" not in post_content
    assert "fetchJcpSessionId" not in post_content
    assert "treatment_group" not in post_content
    assert "Sign in with LinkedIn" in post_content


@pytest.mark.parametrize("experiment", [0, 1])
@pytest.mark.parametrize("include_linkedin_popup", [True, False])
def test_privacy_terms_notice_follows_job_ad(experiment: int, include_linkedin_popup: bool) -> None:
    post_content = build_post_content(
        google_script="<script>google</script>",
        generated_html="<p>body</p>",
        include_linkedin_popup=include_linkedin_popup,
        experiment=experiment,
    )

    assert post_content.endswith("you agree to both.</p>")
    assert post_content.index("<p>body</p>") < post_content.index("Privacy Policy")
    assert 'href="https://jobconnectionsproject.org/privacy-policy/"' in post_content
    assert 'href="https://jobconnectionsproject.org/terms-and-conditions/"' in post_content


def test_experiment_one_keeps_current_treatment_prompt() -> None:
    prompt = build_job_description_system_prompt(1)
    assert "Treatment text" in prompt
    assert "treat4" in prompt


def test_unsupported_experiment_value_is_rejected() -> None:
    with pytest.raises(ValueError, match="Unsupported experiment value"):
        build_job_description_system_prompt(9)

    with pytest.raises(ValueError, match="Unsupported experiment value"):
        build_post_content(
            google_script="<script>google</script>",
            generated_html="<p>body</p>",
            include_linkedin_popup=True,
            experiment=9,
        )


def test_default_jobs_output_path_matches_script_style() -> None:
    output_path = default_jobs_output_path(
        occupation_title="Graphic Designer",
        location="Seattle, WA",
        now=datetime(2026, 4, 21),
    )

    assert str(output_path) == "4-21-2026_graphic_designer_seattle,_wa_jobs.csv"


def test_survey_url_is_encoded_and_replaces_multiple_links() -> None:
    original_ad_url = "https://utah.peopleadmin.com/postings/200835"
    survey_url = build_survey_url(original_ad_url)
    similar_jobs_url = build_similar_jobs_url("software")
    similar_jobs_survey_url = build_similar_jobs_survey_url(similar_jobs_url)
    html = """
    <a href="https://jobconnectionsproject.org/survey/">Apply now</a>
    <a href="/survey/">Continue</a>
    <form action="/survey/"></form>
    <a class="elementor-button elementor-button-link elementor-size-sm" href="https://jobconnectionsproject.org/?s=software">
      <span class="elementor-button-content-wrapper">
        <span class="elementor-button-text">No longer interested, show me similar positions</span>
      </span>
    </a>
    """.strip()

    updated_html = insert_navigation_urls_into_post_html(html, survey_url, similar_jobs_url)

    assert survey_url == "https://jobconnectionsproject.org/survey/?ad_url=https%3A%2F%2Futah.peopleadmin.com%2Fpostings%2F200835"
    assert 'href="https://jobconnectionsproject.org/survey/"' not in updated_html
    assert 'href="/survey/"' not in updated_html
    assert 'action="/survey/"' not in updated_html
    assert updated_html.count(survey_url) == 3
    assert similar_jobs_survey_url in updated_html
    assert 'href="https://jobconnectionsproject.org/?s=software"' not in updated_html
    assert "No longer interested, show me similar positions" in updated_html


def test_wordpress_post_meta_contains_elementor_ready_urls() -> None:
    original_ad_url = "https://utah.peopleadmin.com/postings/200835"
    similar_jobs_url = "https://jobconnectionsproject.org/?s=human%20resources%20TX"

    meta = build_wordpress_post_meta(original_ad_url, similar_jobs_url)

    assert meta["footnotes"] == original_ad_url
    assert meta["survey_url"] == (
        "https://jobconnectionsproject.org/survey/?ad_url="
        "https%3A%2F%2Futah.peopleadmin.com%2Fpostings%2F200835"
    )
    assert meta["similar_jobs_url"] == similar_jobs_url
    assert meta["similar_jobs_survey_url"] == (
        "https://jobconnectionsproject.org/survey1/?ad_url="
        "https%3A%2F%2Fjobconnectionsproject.org%2F%3Fs%3Dhuman%2520resources%2520TX"
    )


def test_extract_state_abbreviation_from_abbreviated_location() -> None:
    assert extract_state_abbreviation("Shenandoah, TX, US") == "TX"


def test_extract_state_abbreviation_from_full_state_name() -> None:
    assert extract_state_abbreviation("Austin, Texas, United States") == "TX"


def test_similar_jobs_term_normalizer_allows_two_words() -> None:
    normalized = _normalize_similar_jobs_term("software engineer", fallback="Software Engineer")
    assert normalized == "software engineer"


def test_similar_jobs_term_normalizer_unjams_fallback_title_words() -> None:
    normalized = _normalize_similar_jobs_term("softwareengineer", fallback="Software Engineer")
    assert normalized == "software engineer"


def test_build_similar_jobs_query_uses_gpt_term_and_state_abbreviation() -> None:
    query = build_similar_jobs_query("Human Resources", "Shenandoah, TX, US")
    assert query == "Human Resources TX"


def test_build_similar_jobs_url_encodes_job_title_and_state_query() -> None:
    url = build_similar_jobs_url("Human Resources TX")
    assert url == "https://jobconnectionsproject.org/?s=Human%20Resources%20TX"


def test_navigation_urls_are_not_injected_when_post_has_no_existing_links() -> None:
    survey_url = build_survey_url("https://example.com/original-job")
    similar_jobs_url = build_similar_jobs_url("software")
    html = (
        "The Job Connections Project is a non-profit company that advertises open positions for other companies. "
        "Please read the hiring company's job ad below, then click 'Continue'."
    )

    updated_html = insert_navigation_urls_into_post_html(html, survey_url, similar_jobs_url)

    assert updated_html == html


def test_similar_jobs_button_can_be_retargeted_through_survey1_placeholder() -> None:
    survey_url = build_survey_url("https://example.com/original-job")
    similar_jobs_url = build_similar_jobs_url("software")
    similar_jobs_survey_url = build_similar_jobs_survey_url(similar_jobs_url)
    html = """
    <a class="elementor-button elementor-button-link elementor-size-sm" href="https://jobconnectionsproject.org/survey1/">
      <span class="elementor-button-content-wrapper">
        <span class="elementor-button-text">No longer interested, show me similar positions</span>
      </span>
    </a>
    """.strip()

    updated_html = insert_navigation_urls_into_post_html(html, survey_url, similar_jobs_url)

    assert similar_jobs_survey_url in updated_html
    assert 'href="https://jobconnectionsproject.org/survey1/"' not in updated_html


def test_calculate_hours_old_is_non_negative() -> None:
    hours_old = calculate_hours_old("04/21/2026", now=datetime(2026, 4, 20, 12, 0, 0))
    assert hours_old == 0


def test_load_environment_populates_required_settings(tmp_path, monkeypatch) -> None:
    env_path = tmp_path / ".env"
    env_path.write_text(
        "\n".join(
            [
                "WORDPRESS_BASE_URL=https://example.com",
                "WORDPRESS_USERNAME=user1",
                "WORDPRESS_APP_PASSWORD=pass1",
                "WORDPRESS_FEATURED_MEDIA_ID=1807",
                "GITHUB_MODELS_TOKEN=github-token",
                "GEMINI_API_KEY=gemini-token",
            ]
        ),
        encoding="utf-8",
    )

    for key in [
        "WORDPRESS_BASE_URL",
        "WORDPRESS_USERNAME",
        "WORDPRESS_APP_PASSWORD",
        "WORDPRESS_FEATURED_MEDIA_ID",
        "GITHUB_MODELS_TOKEN",
        "GEMINI_API_KEY",
        "GITHUB_MODELS_ENDPOINT",
        "GITHUB_MODELS_MODEL",
        "GEMINI_MODEL",
    ]:
        monkeypatch.delenv(key, raising=False)

    load_environment(env_path)

    wordpress = load_wordpress_settings()
    github = load_github_models_settings()
    gemini = load_gemini_settings()

    assert wordpress.posts_endpoint == "https://example.com/wp-json/wp/v2/posts"
    assert wordpress.featured_media_id == 1807
    assert github.token == "github-token"
    assert gemini.api_key == "gemini-token"


def test_settings_can_load_from_shell_environment_without_dotenv(monkeypatch) -> None:
    monkeypatch.setenv("WORDPRESS_BASE_URL", "https://example.com")
    monkeypatch.setenv("WORDPRESS_USERNAME", "user1")
    monkeypatch.setenv("WORDPRESS_APP_PASSWORD", "pass1")
    monkeypatch.setenv("WORDPRESS_FEATURED_MEDIA_ID", "1807")
    monkeypatch.setenv("GITHUB_MODELS_TOKEN", "github-token")
    monkeypatch.setenv("GEMINI_API_KEY", "gemini-token")
    monkeypatch.delenv("GITHUB_MODELS_ENDPOINT", raising=False)
    monkeypatch.delenv("GITHUB_MODELS_MODEL", raising=False)
    monkeypatch.delenv("GEMINI_MODEL", raising=False)

    wordpress = load_wordpress_settings()
    github = load_github_models_settings()
    gemini = load_gemini_settings()

    assert wordpress.posts_endpoint == "https://example.com/wp-json/wp/v2/posts"
    assert wordpress.featured_media_id == 1807
    assert github.token == "github-token"
    assert gemini.api_key == "gemini-token"


def test_cli_parser_accepts_get_jobs_no_linkedin_flag() -> None:
    parser = build_parser()
    args = parser.parse_args(
        [
            "get-jobs",
            "--occupation-title",
            "Graphic Designer",
            "--date-posted",
            "04/21/2026",
            "--location",
            "Seattle, WA",
            "--experiment",
            "1",
            "--no-linkedin",
        ]
    )

    assert args.no_linkedin is True
    assert args.command == "get-jobs"
    assert args.experiment == 1


def test_get_jobs_requires_experiment_flag() -> None:
    parser = build_parser()

    with pytest.raises(SystemExit):
        parser.parse_args(
            [
                "get-jobs",
                "--occupation-title",
                "Graphic Designer",
                "--date-posted",
                "04/21/2026",
                "--location",
                "Seattle, WA",
            ]
        )


def test_cli_parser_accepts_clean_json_data_command() -> None:
    parser = build_parser()
    args = parser.parse_args(
        [
            "clean-json-data",
            "--sessions",
            "sessions.json",
            "--output",
            "merged.parquet",
        ]
    )

    assert args.command == "clean-json-data"
    assert args.sessions == "sessions.json"


def test_cli_parser_accepts_expiration_history_path() -> None:
    parser = build_parser()
    args = parser.parse_args(
        [
            "check-job-expiration",
            "--status",
            "publish",
            "--history-path",
            "expiration-history.json",
        ]
    )

    assert args.command == "check-job-expiration"
    assert args.history_path == "expiration-history.json"


def test_cli_parser_accepts_expiration_max_posts_to_check() -> None:
    parser = build_parser()
    args = parser.parse_args(
        [
            "check-job-expiration",
            "--status",
            "publish",
            "--max-posts-to-check",
            "12",
        ]
    )

    assert args.command == "check-job-expiration"
    assert args.max_posts_to_check == 12


def test_top_level_help_lists_commands_and_command_help_hint(capsys) -> None:
    parser = build_parser()

    with pytest.raises(SystemExit):
        parser.parse_args(["--help"])

    help_text = capsys.readouterr().out
    assert "clean-json-data" in help_text
    assert "get-jobs" in help_text
    assert "check-job-expiration" in help_text
    assert "jcp-data-manager <command> --help" in help_text


def test_old_legacy_usage_without_command_is_rejected() -> None:
    parser = build_parser()

    with pytest.raises(SystemExit):
        parser.parse_args(["--sessions", "sessions.json", "--output", "merged.parquet"])


def test_run_expiration_check_adds_probabilities_before_is_invalid(monkeypatch) -> None:
    monkeypatch.setattr(
        "jcp_data_manager.expiration.fetch_wordpress_posts",
        lambda settings, status="draft", per_page=100: [
            {"id": 101, "meta": {"footnotes": "https://example.com/job"}},
        ],
    )
    monkeypatch.setattr(
        "jcp_data_manager.expiration.fetch_footnote_metadata",
        lambda df: df.with_columns(
            pl.Series("response_code", [200]),
            pl.Series("direct_url_html", ["<html>ok</html>"]),
            pl.Series("request_error", [None]),
        ),
    )
    monkeypatch.setattr(
        "jcp_data_manager.expiration.score_soft_404_probabilities",
        lambda html_pages, settings: [0.25],
    )

    report = run_expiration_check(
        wordpress_settings=WordPressSettings(
            base_url="https://example.com",
            username="user1",
            app_password="pass1",
            featured_media_id=1807,
        ),
        gemini_settings=GeminiSettings(
            api_key="gemini-token",
            model="gemini-2.5-flash-lite",
        ),
        privatize_invalid=False,
    )

    assert "prob_soft_404" in report.columns
    assert "is_invalid" in report.columns
    assert report["prob_soft_404"].to_list() == [0.25]
    assert report["is_invalid"].to_list() == [0]


def test_html_to_readable_text_removes_hidden_clutter_and_truncates() -> None:
    html = """
    <html>
      <head>
        <style>.hidden { display: none; }</style>
        <script>console.log("ignore me")</script>
      </head>
      <body>
        <header>Site Header</header>
        <nav>Main Nav</nav>
        <main>
          <h1>Software Engineer</h1>
          <p>Apply now for this role.</p>
        </main>
        <footer>Footer links</footer>
      </body>
    </html>
    """

    readable = html_to_readable_text(html, max_chars=30)

    assert "console.log" not in readable
    assert "Site Header" not in readable
    assert "Main Nav" not in readable
    assert "Footer links" not in readable
    assert readable == "Software Engineer Apply now fo"


def test_score_soft_404_probabilities_batches_ambiguous_pages_once(monkeypatch) -> None:
    batch_calls: list[list[dict[str, object]]] = []

    def fake_batch(records, settings):
        batch_calls.append(records)
        return {
            records[0]["index"]: 0.2,
            records[1]["index"]: 0.8,
        }

    monkeypatch.setattr(
        "jcp_data_manager.expiration._gemini_batch_score_soft_404_probabilities",
        fake_batch,
    )

    probabilities = score_soft_404_probabilities(
        [
            {
                "index": 0,
                "title": "First role",
                "direct_url": "https://example.com/1",
                "response_code": 200,
                "readable_text": "Careers page with some neutral text only.",
            },
            {
                "index": 1,
                "title": "Second role",
                "direct_url": "https://example.com/2",
                "response_code": 200,
                "readable_text": "Another careers page with neutral language only.",
            },
            {
                "index": 2,
                "title": "Expired role",
                "direct_url": "https://example.com/3",
                "response_code": 200,
                "readable_text": "This posting is no longer available.",
            },
            {
                "index": 3,
                "title": "Missing role",
                "direct_url": "https://example.com/4",
                "response_code": 404,
                "readable_text": "",
            },
        ],
        GeminiSettings(api_key="gemini-token", model="gemini-2.5-flash-lite"),
    )

    assert len(batch_calls) == 1
    assert [record["index"] for record in batch_calls[0]] == [0, 1]
    assert probabilities == [0.2, 0.8, 0.98, 1.0]


def test_score_soft_404_probabilities_falls_back_on_gemini_error(monkeypatch) -> None:
    def fake_batch(records, settings):
        raise RuntimeError("429 RESOURCE_EXHAUSTED retry_delay 38")

    monkeypatch.setattr(
        "jcp_data_manager.expiration._gemini_batch_score_soft_404_probabilities",
        fake_batch,
    )

    probabilities = score_soft_404_probabilities(
        [
            {
                "index": 0,
                "title": "Ambiguous role",
                "direct_url": "https://example.com/1",
                "response_code": 200,
                "readable_text": "Neutral content that needs model review.",
            }
        ],
        GeminiSettings(api_key="gemini-token", model="gemini-2.5-flash-lite"),
    )

    assert probabilities == [0.5]


def test_score_soft_404_probabilities_treats_redirect_statuses_as_soft_404_candidates(monkeypatch) -> None:
    batch_calls: list[list[dict[str, object]]] = []

    def fake_batch(records, settings):
        batch_calls.append(records)
        return {record["index"]: 0.2 + (0.1 * idx) for idx, record in enumerate(records)}

    monkeypatch.setattr(
        "jcp_data_manager.expiration._gemini_batch_score_soft_404_probabilities",
        fake_batch,
    )

    probabilities = score_soft_404_probabilities(
        [
            {
                "index": 0,
                "title": "Redirected role one",
                "direct_url": "https://example.com/1",
                "response_code": 301,
                "readable_text": "Some neutral careers content after redirect.",
            },
            {
                "index": 1,
                "title": "Redirected role two",
                "direct_url": "https://example.com/2",
                "response_code": 302,
                "readable_text": "Another neutral careers page after redirect.",
            },
            {
                "index": 2,
                "title": "Redirected role three",
                "direct_url": "https://example.com/3",
                "response_code": 303,
                "readable_text": "Third neutral careers page after redirect.",
            },
            {
                "index": 3,
                "title": "Redirected role four",
                "direct_url": "https://example.com/4",
                "response_code": 307,
                "readable_text": "Fourth neutral careers page after redirect.",
            },
            {
                "index": 4,
                "title": "Redirected role five",
                "direct_url": "https://example.com/5",
                "response_code": 308,
                "readable_text": "Fifth neutral careers page after redirect.",
            },
        ],
        GeminiSettings(api_key="gemini-token", model="gemini-2.5-flash-lite"),
    )

    assert len(batch_calls) == 1
    assert [record["response_code"] for record in batch_calls[0]] == [301, 302, 303, 307, 308]
    assert probabilities == pytest.approx([0.2, 0.3, 0.4, 0.5, 0.6])


def test_run_expiration_check_does_not_auto_invalidate_302_when_probability_is_low(monkeypatch) -> None:
    monkeypatch.setattr(
        "jcp_data_manager.expiration.fetch_wordpress_posts",
        lambda settings, status="draft", per_page=100: [
            {"id": 101, "meta": {"footnotes": "https://example.com/job"}, "title": {"rendered": "Example role"}},
        ],
    )
    monkeypatch.setattr(
        "jcp_data_manager.expiration.fetch_footnote_metadata",
        lambda df: df.with_columns(
            pl.Series("response_code", [302]),
            pl.Series("direct_url_html", ["<html>redirected job content</html>"]),
            pl.Series("request_error", [None]),
        ),
    )
    monkeypatch.setattr(
        "jcp_data_manager.expiration.score_soft_404_probabilities",
        lambda records, settings: [0.25],
    )

    report = run_expiration_check(
        wordpress_settings=WordPressSettings(
            base_url="https://example.com",
            username="user1",
            app_password="pass1",
            featured_media_id=1807,
        ),
        gemini_settings=GeminiSettings(
            api_key="gemini-token",
            model="gemini-2.5-flash-lite",
        ),
        privatize_invalid=False,
    )

    assert report["response_code"].to_list() == [302]
    assert report["prob_soft_404"].to_list() == [0.25]
    assert report["is_invalid"].to_list() == [0]


def test_run_expiration_check_uses_history_to_prioritize_oldest_and_preserve_privatized_rows(
    monkeypatch,
) -> None:
    history_df = pl.DataFrame(
        [
            {
                "post_id": 1,
                "title": "Older checked role",
                "footnote": "https://example.com/1",
                "checked_status": "publish",
                "checked_at": "2026-05-01T00:00:00+00:00",
                "response_code": 200,
                "request_error": None,
                "prob_soft_404": 0.2,
                "is_invalid": 0,
                "is_valid": 1,
                "was_privatized": 0,
                "private_status_code": None,
                "private_error": None,
            },
            {
                "post_id": 2,
                "title": "Recently checked role",
                "footnote": "https://example.com/2",
                "checked_status": "publish",
                "checked_at": "2026-05-04T00:00:00+00:00",
                "response_code": 200,
                "request_error": None,
                "prob_soft_404": 0.1,
                "is_invalid": 0,
                "is_valid": 1,
                "was_privatized": 0,
                "private_status_code": None,
                "private_error": None,
            },
            {
                "post_id": 4,
                "title": "Privatized role",
                "footnote": "https://example.com/4",
                "checked_status": "publish",
                "checked_at": "2026-05-02T00:00:00+00:00",
                "response_code": 404,
                "request_error": None,
                "prob_soft_404": 1.0,
                "is_invalid": 1,
                "is_valid": 0,
                "was_privatized": 1,
                "private_status_code": 200,
                "private_error": None,
            },
        ]
    )
    written_frames: dict[str, pl.DataFrame] = {}

    monkeypatch.setattr(
        "jcp_data_manager.expiration.load_expiration_history",
        lambda path: history_df,
    )
    monkeypatch.setattr(
        "jcp_data_manager.expiration.write_dataframe",
        lambda df, path: written_frames.__setitem__(str(path), df),
    )

    monkeypatch.setattr(
        "jcp_data_manager.expiration.fetch_wordpress_posts",
        lambda settings, status="publish", per_page=100: [
            {"id": 1, "title": {"rendered": "Older checked role"}, "meta": {"footnotes": "https://example.com/1"}},
            {"id": 2, "title": {"rendered": "Recently checked role"}, "meta": {"footnotes": "https://example.com/2"}},
            {"id": 3, "title": {"rendered": "Never checked role"}, "meta": {"footnotes": "https://example.com/3"}},
            {"id": 4, "title": {"rendered": "Privatized role"}, "meta": {"footnotes": "https://example.com/4"}},
        ],
    )
    monkeypatch.setattr(
        "jcp_data_manager.expiration.fetch_footnote_metadata",
        lambda df: df.with_columns(
            pl.Series("response_code", [200] * df.height),
            pl.Series("direct_url_html", ["<html>active job content</html>"] * df.height),
            pl.Series("request_error", [None] * df.height),
        ),
    )
    monkeypatch.setattr(
        "jcp_data_manager.expiration.score_soft_404_probabilities",
        lambda records, settings: [0.1] * len(records),
    )

    report = run_expiration_check(
        wordpress_settings=WordPressSettings(
            base_url="https://example.com",
            username="user1",
            app_password="pass1",
            featured_media_id=1807,
        ),
        gemini_settings=GeminiSettings(
            api_key="gemini-token",
            model="gemini-2.5-flash-lite",
        ),
        status="publish",
        privatize_invalid=False,
        history_path="expiration-history.json",
        max_posts_to_check=2,
    )

    assert report["post_id"].to_list() == [3, 1]
    assert "checked_at" in report.columns
    assert "is_valid" in report.columns
    assert report["is_invalid"].to_list() == [0, 0]

    updated_history = written_frames["expiration-history.json"]
    assert sorted(updated_history["post_id"].to_list()) == [1, 2, 3, 4]

    never_checked_row = updated_history.filter(pl.col("post_id") == 3).row(0, named=True)
    older_checked_row = updated_history.filter(pl.col("post_id") == 1).row(0, named=True)
    recent_row = updated_history.filter(pl.col("post_id") == 2).row(0, named=True)
    privatized_row = updated_history.filter(pl.col("post_id") == 4).row(0, named=True)

    assert never_checked_row["checked_status"] == "publish"
    assert never_checked_row["is_valid"] == 1
    assert older_checked_row["checked_at"] != "2026-05-01T00:00:00+00:00"
    assert recent_row["checked_at"] == "2026-05-04T00:00:00+00:00"
    assert privatized_row["was_privatized"] == 1


def test_run_expiration_check_allows_output_and_history_to_share_one_path(monkeypatch) -> None:
    history_df = pl.DataFrame(
        [
            {
                "post_id": 1,
                "title": "Existing role",
                "footnote": "https://example.com/1",
                "checked_status": "publish",
                "checked_at": "2026-05-01T00:00:00+00:00",
                "response_code": 200,
                "request_error": None,
                "prob_soft_404": 0.1,
                "is_invalid": 0,
                "is_valid": 1,
                "was_privatized": 0,
                "private_status_code": None,
                "private_error": None,
            }
        ]
    )
    writes: list[tuple[str, pl.DataFrame]] = []

    monkeypatch.setattr(
        "jcp_data_manager.expiration.load_expiration_history",
        lambda path: history_df,
    )
    monkeypatch.setattr(
        "jcp_data_manager.expiration.write_dataframe",
        lambda df, path: writes.append((str(path), df)),
    )
    monkeypatch.setattr(
        "jcp_data_manager.expiration.fetch_wordpress_posts",
        lambda settings, status="publish", per_page=100: [
            {"id": 1, "title": {"rendered": "Existing role"}, "meta": {"footnotes": "https://example.com/1"}},
            {"id": 2, "title": {"rendered": "New role"}, "meta": {"footnotes": "https://example.com/2"}},
        ],
    )
    monkeypatch.setattr(
        "jcp_data_manager.expiration.fetch_footnote_metadata",
        lambda df: df.with_columns(
            pl.Series("response_code", [200] * df.height),
            pl.Series("direct_url_html", ["<html>active job content</html>"] * df.height),
            pl.Series("request_error", [None] * df.height),
        ),
    )
    monkeypatch.setattr(
        "jcp_data_manager.expiration.score_soft_404_probabilities",
        lambda records, settings: [0.1] * len(records),
    )

    report = run_expiration_check(
        wordpress_settings=WordPressSettings(
            base_url="https://example.com",
            username="user1",
            app_password="pass1",
            featured_media_id=1807,
        ),
        gemini_settings=GeminiSettings(
            api_key="gemini-token",
            model="gemini-2.5-flash-lite",
        ),
        status="publish",
        privatize_invalid=False,
        history_path="expiration-state.json",
        output_path="expiration-state.json",
        max_posts_to_check=2,
    )

    assert report["post_id"].to_list() == [2, 1]
    assert len(writes) == 1
    assert writes[0][0] == "expiration-state.json"
    assert sorted(writes[0][1]["post_id"].to_list()) == [1, 2]
    assert "checked_at" in writes[0][1].columns
