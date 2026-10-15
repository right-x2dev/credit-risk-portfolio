"""Configuration loading: `.env` for connection/secrets, `configs/*.yaml` for business rules.

Nothing in this module hard-codes paths, passwords, ports or thresholds; everything is read
from the environment or the YAML files so that the same code runs locally, in Docker and in tests.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml
from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parents[2]

REQUIRED_PROJECT_KEYS = ("project", "sources", "relationships", "quality", "risk", "segments")
REQUIRED_MODEL_KEYS = ("split", "models", "selection", "calibration", "threshold", "evaluation", "explain")


class ConfigError(ValueError):
    """Raised when a configuration file is missing or lacks required keys."""


def _resolve(path_str: str) -> Path:
    path = Path(path_str)
    return path if path.is_absolute() else (PROJECT_ROOT / path).resolve()


def load_yaml(path: Path, required_keys: tuple[str, ...] = ()) -> dict[str, Any]:
    if not path.exists():
        raise ConfigError(f"config file not found: {path}")
    with path.open(encoding="utf-8") as fh:
        data = yaml.safe_load(fh) or {}
    missing = [k for k in required_keys if k not in data]
    if missing:
        raise ConfigError(f"{path.name} is missing required keys: {missing}")
    return data


@dataclass(frozen=True)
class Settings:
    pg_host: str
    pg_port: int
    pg_db: str
    pg_user: str
    pg_password: str
    data_dir: Path
    model_dir: Path
    random_seed: int
    config_dir: Path
    reports_dir: Path
    export_dir: Path
    project: dict[str, Any] = field(repr=False)
    model: dict[str, Any] = field(repr=False)
    features: dict[str, Any] = field(repr=False)

    @property
    def conninfo(self) -> str:
        return (
            f"host={self.pg_host} port={self.pg_port} dbname={self.pg_db} "
            f"user={self.pg_user} password={self.pg_password}"
        )

    @property
    def feature_version(self) -> str:
        return str(self.project["project"]["feature_version"])

    @property
    def sql_dir(self) -> Path:
        return PROJECT_ROOT / "sql"


def load_settings(env_file: Path | None = None, overrides: dict[str, str] | None = None) -> Settings:
    """Build Settings from `.env` (if present), process env vars and optional overrides."""
    load_dotenv(env_file or PROJECT_ROOT / ".env", override=False)
    env = dict(os.environ)
    if overrides:
        env.update(overrides)

    def get(name: str, default: str | None = None) -> str:
        value = env.get(name, default)
        if value is None or value == "":
            raise ConfigError(f"environment variable {name} is not set (see .env.example)")
        return value

    config_dir = _resolve(env.get("CONFIG_DIR", "configs"))
    return Settings(
        pg_host=get("POSTGRES_HOST", "localhost"),
        pg_port=int(get("POSTGRES_PORT", "5432")),
        pg_db=get("POSTGRES_DB"),
        pg_user=get("POSTGRES_USER"),
        pg_password=get("POSTGRES_PASSWORD"),
        data_dir=_resolve(get("DATA_DIR", "./data/raw")),
        model_dir=_resolve(get("MODEL_DIR", "./models")),
        random_seed=int(get("RANDOM_SEED", "42")),
        config_dir=config_dir,
        reports_dir=_resolve(env.get("REPORTS_DIR", "reports")),
        export_dir=_resolve(env.get("POWERBI_EXPORT_DIR", "data/processed/powerbi")),
        project=load_yaml(config_dir / "project.yaml", REQUIRED_PROJECT_KEYS),
        model=load_yaml(config_dir / "model.yaml", REQUIRED_MODEL_KEYS),
        features=load_yaml(config_dir / "features.yaml", ("exclude", "categorical_max_cardinality")),
    )


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return load_settings()
