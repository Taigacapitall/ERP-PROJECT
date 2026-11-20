from __future__ import annotations

import json
import logging
import os
from pathlib import Path

import pytest

from vorpal.core.config import AppConfig, _parse_simple_yaml
from vorpal.core.errors import ValidationError

SECRET = "SUPER-SECRET-123"


@pytest.fixture(autouse=True)
def clean_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for key in list(os.environ):
        if key.startswith(("VORPAL_", "APP_")):
            monkeypatch.delenv(key)


def test_defaults_without_file_or_env() -> None:
    cfg = AppConfig.load()
    assert cfg.env == "dev"
    assert cfg.default_currency == "USD"
    assert cfg.secrets == {}


def test_load_json(tmp_path: Path) -> None:
    f = tmp_path / "c.json"
    f.write_text(
        json.dumps({"env": "prod", "db_url": "sqlite:///x.db", "secrets": {"dgi_token": "abc"}}),
        encoding="utf-8",
    )
    cfg = AppConfig.load(f)
    assert cfg.env == "prod"
    assert cfg.db_url == "sqlite:///x.db"
    assert cfg.secrets == {"dgi_token": "abc"}


@pytest.mark.parametrize("name", ["c.yaml", "c.yml"])
def test_load_yaml_with_section_comments_and_quotes(tmp_path: Path, name: str) -> None:
    f = tmp_path / name
    f.write_text(
        "# commentaire\n"
        "env: test\n"
        "\n"
        "log_level: 'DEBUG'  # fin de ligne\n"
        "secrets:\n"
        '  dgi_token: "tok"\n',
        encoding="utf-8",
    )
    cfg = AppConfig.load(f)
    assert cfg.env == "test"
    assert cfg.log_level == "DEBUG"
    assert cfg.secrets == {"dgi_token": "tok"}


def test_parse_simple_yaml_directly() -> None:
    parsed = _parse_simple_yaml("a: 1\nsec:\n  k: v\nb: 'x'\n")
    assert parsed == {"a": "1", "sec": {"k": "v"}, "b": "x"}


def test_env_overrides_file_and_empty_env_is_ignored(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    f = tmp_path / "c.json"
    f.write_text(json.dumps({"env": "dev", "log_level": "INFO"}), encoding="utf-8")
    monkeypatch.setenv("VORPAL_ENV", "prod")
    monkeypatch.setenv("VORPAL_LOG_LEVEL", "")
    cfg = AppConfig.load(f)
    assert cfg.env == "prod"
    assert cfg.log_level == "INFO"


def test_custom_env_prefix(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("APP_ENV", "staging")
    monkeypatch.setenv("APP_DEFAULT_CURRENCY", "CDF")
    cfg = AppConfig.load(env_prefix="APP_")
    assert cfg.env == "staging"
    assert cfg.default_currency == "CDF"


def test_secrets_from_env_merge_with_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    f = tmp_path / "c.json"
    f.write_text(json.dumps({"secrets": {"a": "1"}}), encoding="utf-8")
    monkeypatch.setenv("VORPAL_SECRET_DGI_TOKEN", "t")
    cfg = AppConfig.load(f)
    assert cfg.secrets == {"a": "1", "dgi_token": "t"}


def test_missing_file_unsupported_format_malformed_json_and_bad_root(tmp_path: Path) -> None:
    with pytest.raises(ValidationError, match="introuvable"):
        AppConfig.load(tmp_path / "absent.json")

    toml = tmp_path / "c.toml"
    toml.write_text("env = 'dev'", encoding="utf-8")
    with pytest.raises(ValidationError, match="non supporté"):
        AppConfig.load(toml)

    bad = tmp_path / "bad.json"
    bad.write_text("{not json", encoding="utf-8")
    with pytest.raises(ValidationError, match="JSON"):
        AppConfig.load(bad)

    root_list = tmp_path / "list.json"
    root_list.write_text("[1, 2]", encoding="utf-8")
    with pytest.raises(ValidationError, match="racine"):
        AppConfig.load(root_list)


def test_unreadable_file_is_wrapped(tmp_path: Path) -> None:
    f = tmp_path / "c.json"
    f.write_bytes(b"\xff\xfe\x00\x80")
    with pytest.raises(ValidationError, match="illisible"):
        AppConfig.load(f)


@pytest.mark.parametrize(
    ("var", "value"),
    [("VORPAL_ENV", "nope"), ("VORPAL_DEFAULT_CURRENCY", "EUR")],
)
def test_invalid_values_rejected(monkeypatch: pytest.MonkeyPatch, var: str, value: str) -> None:
    monkeypatch.setenv(var, value)
    with pytest.raises(ValidationError, match="Configuration invalide"):
        AppConfig.load()


def test_secret_never_in_repr_errors_or_logs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    monkeypatch.setenv("VORPAL_SECRET_DGI_TOKEN", SECRET)
    with caplog.at_level(logging.DEBUG):
        cfg = AppConfig.load()
    assert cfg.secrets["dgi_token"] == SECRET  # accessible par le code
    assert SECRET not in repr(cfg)
    assert SECRET not in str(cfg)
    assert SECRET not in caplog.text

    # une config invalide ne doit pas recracher les valeurs saisies
    monkeypatch.setenv("VORPAL_ENV", "nope")
    with pytest.raises(ValidationError) as exc_info:
        AppConfig.load()
    assert SECRET not in str(exc_info.value)

    f = tmp_path / "c.json"
    f.write_text(json.dumps({"secrets": {"k": 12345678}}), encoding="utf-8")
    monkeypatch.delenv("VORPAL_ENV")
    with pytest.raises(ValidationError) as exc_info2:
        AppConfig.load(f)
    assert "12345678" not in str(exc_info2.value)
