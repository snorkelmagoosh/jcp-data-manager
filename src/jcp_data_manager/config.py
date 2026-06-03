"""Configuration helpers for CLI workflows."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import find_dotenv, load_dotenv


DEFAULT_GITHUB_MODELS_ENDPOINT = "https://models.github.ai/inference"
DEFAULT_GITHUB_MODELS_MODEL = "openai/gpt-4.1-mini"
DEFAULT_GEMINI_MODEL = "gemini-2.5-flash-lite"


@dataclass(frozen=True)
class WordPressSettings:
    base_url: str
    username: str
    app_password: str
    featured_media_id: int

    @property
    def posts_endpoint(self) -> str:
        return f"{self.base_url.rstrip('/')}/wp-json/wp/v2/posts"


@dataclass(frozen=True)
class GithubModelsSettings:
    endpoint: str
    token: str
    model: str


@dataclass(frozen=True)
class GeminiSettings:
    api_key: str
    model: str


def load_environment(env_file: str | Path | None = None) -> None:
    if env_file is not None:
        env_path = Path(env_file)
        if not env_path.exists():
            raise FileNotFoundError(f"Environment file not found: {env_path}")
        load_dotenv(env_path, override=False)
        return

    default_env = find_dotenv(usecwd=True)
    if default_env:
        load_dotenv(default_env, override=False)


def _get_required_env(name: str) -> str:
    value = os.getenv(name)
    if value is None or not value.strip():
        raise ValueError(f"Missing required environment variable: {name}")
    return value.strip()


def load_wordpress_settings(featured_media_id: int | None = None) -> WordPressSettings:
    media_id = featured_media_id
    if media_id is None:
        media_id = int(_get_required_env("WORDPRESS_FEATURED_MEDIA_ID"))

    return WordPressSettings(
        base_url=_get_required_env("WORDPRESS_BASE_URL"),
        username=_get_required_env("WORDPRESS_USERNAME"),
        app_password=_get_required_env("WORDPRESS_APP_PASSWORD"),
        featured_media_id=media_id,
    )


def load_github_models_settings() -> GithubModelsSettings:
    endpoint = os.getenv("GITHUB_MODELS_ENDPOINT", DEFAULT_GITHUB_MODELS_ENDPOINT).strip()
    model = os.getenv("GITHUB_MODELS_MODEL", DEFAULT_GITHUB_MODELS_MODEL).strip()

    return GithubModelsSettings(
        endpoint=endpoint,
        token=_get_required_env("GITHUB_MODELS_TOKEN"),
        model=model,
    )


def load_gemini_settings() -> GeminiSettings:
    model = os.getenv("GEMINI_MODEL", DEFAULT_GEMINI_MODEL).strip()
    return GeminiSettings(
        api_key=_get_required_env("GEMINI_API_KEY"),
        model=model,
    )


@dataclass(frozen=True)
class GoogleIndexingSettings:
    credentials_file: str


def load_google_indexing_settings() -> GoogleIndexingSettings:
    return GoogleIndexingSettings(
        credentials_file=_get_required_env("GOOGLE_INDEXING_CREDENTIALS_FILE"),
    )
