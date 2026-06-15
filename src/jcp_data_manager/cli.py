"""Command line entry point for jcp-data-manager."""

from __future__ import annotations

import argparse
from pathlib import Path
import sys

from .config import (
    load_environment,
    load_gemini_settings,
    load_github_models_settings,
    load_google_indexing_settings,
    load_wordpress_settings,
)
from .expiration import run_expiration_check
from .io import write_dataframe
from .jobs import run_job_posting_pipeline
from .pipeline import run_pipeline


class CliHelpFormatter(argparse.RawTextHelpFormatter):
    pass


def add_enrich_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--sessions", required=True, help="Path to the sessions JSON file.")
    parser.add_argument(
        "--output",
        required=True,
        help="Output path. Supported extensions: .parquet, .csv, .json",
    )
    parser.add_argument(
        "--skip-image-analysis",
        action="store_true",
        help="Skip DeepFace analysis using the picture URL column.",
    )
    parser.add_argument(
        "--skip-name-analysis",
        action="store_true",
        help="Skip gender-guesser and ethnicolr enrichment using given/family names.",
    )
    parser.add_argument(
        "--image-timeout",
        type=int,
        default=20,
        help="Timeout in seconds for downloading profile pictures.",
    )
    parser.add_argument(
        "--skip-face-detection",
        action="store_true",
        help="Pass enforce_detection=False to DeepFace. This makes it so DeepFace will not try and detect race/age/gender if it cannot detect a face.",
    )


def add_job_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--occupation-title", required=True, help="Occupation title to search for.")
    parser.add_argument("--date-posted", required=True, help="Earliest date posted in MM/DD/YYYY format.")
    parser.add_argument("--location", required=True, help="Location to search in.")
    parser.add_argument(
        "--experiment",
        required=True,
        type=int,
        help=(
            "Required experiment mode.\n"
            "  0 = Non-experimental post with no treatment group and no treatment randomization.\n"
            "  1 = Original treatment randomization flow."
        ),
    )
    parser.add_argument("--output", help="Optional output path. Defaults to the script-style CSV filename.")
    parser.add_argument("--env-file", help="Optional path to a .env file.")
    parser.add_argument("--results-wanted", type=int, default=20, help="Number of JobSpy results wanted.")
    parser.add_argument(
        "--skip-post",
        action="store_true",
        help="Scrape and save the enriched jobs output without posting drafts to WordPress.",
    )
    parser.add_argument(
        "--featured-media-id",
        type=int,
        help="Override WORDPRESS_FEATURED_MEDIA_ID for the WordPress posts. The featured media is the post image.",
    )
    parser.add_argument(
        "--keyword-timeout",
        type=int,
        default=10,
        help="Timeout in seconds when checking job pages for qualification keywords.",
    )
    parser.add_argument(
        "--min-days-until-close",
        type=int,
        default=21,
        help=(
            "Minimum days until a job's detected closing date for it to be included.\n"
            "Jobs where a closing date is found and is sooner than this threshold are skipped.\n"
            "Jobs with no detected closing date are always included. Default: 21."
        ),
    )


def add_expiration_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--env-file", help="Optional path to a .env file.")
    parser.add_argument("--output", help="Optional path to save the expiration report.")
    parser.add_argument(
        "--history-path",
        help="Optional path to a state/history file used to prioritize the least recently checked posts and preserve prior results.",
    )
    parser.add_argument("--status", default="draft", help="WordPress post status to inspect.")
    parser.add_argument("--per-page", type=int, default=100, help="Number of WordPress posts to fetch.")
    parser.add_argument(
        "--max-posts-to-check",
        type=int,
        default=20,
        help="Maximum number of posts to check per run. Defaults to 20, prioritizing never-checked and least recently checked posts first.",
    )
    parser.add_argument(
        "--threshold",
        type=float,
        default=0.5,
        help="Probability threshold for classifying a soft 404 page as invalid.",
    )
    parser.add_argument(
        "--skip-private",
        action="store_true",
        help="Generate the invalid-post report without changing invalid posts to private.",
    )
    parser.add_argument(
        "--sleep-seconds",
        type=float,
        default=0.2,
        help="Delay between WordPress status update requests.",
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="CLI toolkit for JCP data management workflows.",
        epilog=(
            "Commands:\n"
            "  clean-json-data       Cleans sessions json file and runs models to tease out race/age/gender from linkedin data.\n"
            "  get-jobs              Scrape jobs, build JCP HTML, and optionally post to WordPress.\n"
            "  check-job-expiration  Review existing WordPress posts for dead or soft-404 links.\n\n"
            "Use 'jcp-data-manager <command> --help' to see all options for a specific command."
        ),
        formatter_class=CliHelpFormatter,
    )
    subparsers = parser.add_subparsers(dest="command", metavar="command")
    subparsers.required = True

    clean_json_parser = subparsers.add_parser(
        "clean-json-data",
        help="Cleans sessions json file and runs models to tease out race/age/gender from linkedin data.",
        description=(
            "Clean JCP sessions JSON export and optionally run image and name analysis on linkedin data.\n\n"
            "Example:\n"
            "  jcp-data-manager clean-json-data --sessions sessions.json --output clean.parquet"
        ),
        formatter_class=CliHelpFormatter,
    )
    add_enrich_arguments(clean_json_parser)
    clean_json_parser.set_defaults(handler=handle_enrich_command)

    jobs_parser = subparsers.add_parser(
        "get-jobs",
        aliases=["post-jobs"],
        help="Scrape jobs, generate JCP HTML, save the output, and optionally create WordPress drafts.",
        description=(
            "Scrape jobs, filter for qualifications, generate JCP-ready HTML, save the results,\n"
            "and optionally create WordPress drafts.\n\n"
            "Experiments:\n"
            "  0 = non-experimental; no treatment group and no treatment randomization.\n"
            "  1 = current treatment randomization flow.\n\n"
            "Example:\n"
            "  jcp-data-manager get-jobs --occupation-title \"Graphic Designer\" --date-posted 04/21/2026 --location \"Seattle, WA\" --experiment 1"
        ),
        formatter_class=CliHelpFormatter,
    )
    add_job_arguments(jobs_parser)
    jobs_parser.set_defaults(handler=handle_get_jobs_command)

    expiration_parser = subparsers.add_parser(
        "check-job-expiration",
        aliases=["expire-jobs"],
        help="Review WordPress drafts and privatize posts whose source links appear invalid.",
        description=(
            "Inspect existing WordPress posts, fetch their source URLs, score soft-404 probability,\n"
            "and optionally move invalid posts to private.\n\n"
            "Example:\n"
            "  jcp-data-manager check-job-expiration --status draft --output invalid-posts.csv"
        ),
        formatter_class=CliHelpFormatter,
    )
    add_expiration_arguments(expiration_parser)
    expiration_parser.set_defaults(handler=handle_check_job_expiration_command)

    return parser


def handle_enrich_command(args: argparse.Namespace) -> None:
    merged = run_pipeline(
        sessions_path=args.sessions,
        with_image_analysis=not args.skip_image_analysis,
        with_name_analysis=not args.skip_name_analysis,
        image_timeout=args.image_timeout,
        enforce_detection=not args.skip_face_detection,
    )
    write_dataframe(merged, args.output)


def handle_get_jobs_command(args: argparse.Namespace) -> None:
    load_environment(args.env_file)

    github_settings = load_github_models_settings()
    wordpress_settings = None if args.skip_post else load_wordpress_settings(featured_media_id=args.featured_media_id)
    try:
        google_credentials_file = load_google_indexing_settings().credentials_file
    except ValueError:
        google_credentials_file = None

    jobs_df, output_path = run_job_posting_pipeline(
        occupation_title=args.occupation_title,
        date_posted=args.date_posted,
        location=args.location,
        github_settings=github_settings,
        wordpress_settings=wordpress_settings,
        output_path=args.output,
        results_wanted=args.results_wanted,
        experiment=args.experiment,
        skip_post=args.skip_post,
        keyword_timeout=args.keyword_timeout,
        min_days_until_close=args.min_days_until_close,
        google_credentials_file=google_credentials_file,
    )

    print(f"Saved jobs output to {output_path}")
    print(f"Jobs with qualifications and direct links found: {jobs_df.height}")
    if args.skip_post:
        print("Skipped WordPress posting.")
    elif "wordpress_status_code" in jobs_df.columns:
        failures = sum(
            1
            for value in jobs_df["wordpress_status_code"].to_list()
            if value is not None and int(value) >= 400
        )
        print(f"WordPress post attempts: {jobs_df.height}")
        print(f"WordPress posting failures: {failures}")


def handle_check_job_expiration_command(args: argparse.Namespace) -> None:
    load_environment(args.env_file)
    wordpress_settings = load_wordpress_settings()
    gemini_settings = load_gemini_settings()
    try:
        google_credentials_file = load_google_indexing_settings().credentials_file
    except ValueError:
        google_credentials_file = None

    report = run_expiration_check(
        wordpress_settings=wordpress_settings,
        gemini_settings=gemini_settings,
        status=args.status,
        per_page=args.per_page,
        threshold=args.threshold,
        privatize_invalid=not args.skip_private,
        sleep_seconds=args.sleep_seconds,
        output_path=args.output,
        history_path=args.history_path,
        max_posts_to_check=args.max_posts_to_check,
        google_credentials_file=google_credentials_file,
    )

    invalid_count = int(report["is_invalid"].sum()) if "is_invalid" in report.columns and report.height else 0
    print(f"Posts checked: {report.height}")
    print(f"Invalid posts found: {invalid_count}")
    same_output_and_history = bool(args.output and args.history_path and Path(args.output) == Path(args.history_path))
    if same_output_and_history:
        print(f"Saved expiration report/history to {args.output}")
    elif args.output:
        print(f"Saved expiration report to {args.output}")
    if args.history_path and not same_output_and_history:
        print(f"Saved expiration history to {args.history_path}")


def main(argv: list[str] | None = None) -> None:
    cli_args = list(sys.argv[1:] if argv is None else argv)

    parser = build_parser()
    args = parser.parse_args(cli_args)

    args.handler(args)


if __name__ == "__main__":
    main()
