from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field
from pydantic import ValidationError as PydanticValidationError

from vorpal.core.errors import ValidationError


class AppConfig(BaseModel):
    env: Literal["dev", "test", "staging", "prod"] = "dev"
    db_url: str = "sqlite:///vorpal.db"
    log_level: str = "INFO"
    default_currency: Literal["USD", "CDF"] = "USD"
    # repr=False : les secrets n'apparaissent jamais dans repr()/str()/logs.
    secrets: dict[str, str] = Field(default_factory=dict, repr=False)

    @classmethod
    def load(
        cls,
        config_path: str | Path | None = None,
        env_prefix: str = "VORPAL_",
    ) -> AppConfig:
        data: dict[str, object] = {}
        if config_path is not None:
            path = Path(config_path)
            if not path.exists():
                raise ValidationError(f"Fichier de configuration introuvable: {path}")
            try:
                raw_text = path.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError) as exc:
                raise ValidationError(f"Fichier de configuration illisible: {path}") from exc
            if path.suffix.lower() == ".json":
                try:
                    parsed = json.loads(raw_text)
                except json.JSONDecodeError as exc:
                    raise ValidationError(
                        f"Fichier JSON malformé: {path} (ligne {exc.lineno}, colonne {exc.colno})"
                    ) from exc
            elif path.suffix.lower() in {".yaml", ".yml"}:
                parsed = _parse_simple_yaml(raw_text)
            else:
                raise ValidationError(f"Format de config non supporté: {path.suffix}")
            if not isinstance(parsed, dict):
                raise ValidationError("La configuration racine doit être un objet.")
            data.update(parsed)

        env_map = {
            f"{env_prefix}ENV": "env",
            f"{env_prefix}DB_URL": "db_url",
            f"{env_prefix}LOG_LEVEL": "log_level",
            f"{env_prefix}DEFAULT_CURRENCY": "default_currency",
        }
        for env_key, field_name in env_map.items():
            val = os.environ.get(env_key)
            if val is not None and val != "":
                data[field_name] = val

        raw_secrets = data.get("secrets")
        secrets: dict[str, object] = dict(raw_secrets) if isinstance(raw_secrets, dict) else {}
        secret_prefix = f"{env_prefix}SECRET_"
        for k, v in os.environ.items():
            if k.startswith(secret_prefix):
                secrets[k[len(secret_prefix) :].lower()] = v
        data["secrets"] = secrets

        try:
            return cls.model_validate(data)
        except PydanticValidationError as exc:
            # Uniquement champ + message : jamais les valeurs saisies (elles peuvent être des secrets).
            details = "; ".join(
                f"{'.'.join(str(part) for part in err['loc'])}: {err['msg']}" for err in exc.errors()
            )
            raise ValidationError(f"Configuration invalide: {details}") from None


def _parse_simple_yaml(text: str) -> dict[str, object]:
    """Parseur YAML minimal (clés simples + section secrets:) sans dépendance externe."""
    result: dict[str, object] = {}
    current_section: str | None = None
    for raw_line in text.splitlines():
        line = raw_line.split("#", 1)[0].rstrip()
        if not line.strip():
            continue
        if not line.startswith(" ") and line.endswith(":"):
            section = line[:-1].strip()
            result[section] = {}
            current_section = section
            continue
        if line.startswith("  ") and current_section is not None and ":" in line:
            k, v = line.strip().split(":", 1)
            sec_dict = result[current_section]
            if isinstance(sec_dict, dict):
                sec_dict[k.strip()] = v.strip().strip('"').strip("'")
            continue
        if ":" in line:
            current_section = None
            k, v = line.split(":", 1)
            result[k.strip()] = v.strip().strip('"').strip("'")
    return result
