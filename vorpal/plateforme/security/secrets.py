"""Chiffrement par enveloppe AES-256-GCM (brique B2, ADR-006).

Chaque secret est chiffré avec une clé de données aléatoire (DEK) ; la DEK est elle-même chiffrée
par une clé maître identifiée (KEK). La rotation re-chiffre seulement la DEK : le secret n'est
jamais déchiffré par la rotation et aucune donnée n'est perdue. Le contexte (ex. « entreprise:42 »)
est authentifié : un jeton déplacé vers un autre contexte est refusé.

Clés maîtres : variable ``VORPAL_MASTER_KEY`` (``id:base64``, clé courante) et optionnellement
``VORPAL_MASTER_KEY_PREVIOUS`` (liste ``id:base64`` séparée par des virgules), ou fichier
``VORPAL_MASTER_KEY_FILE`` (une clé ``id:base64`` par ligne, la première est la courante,
permissions 0600). Aucune clé ne figure dans le dépôt, la base ou les journaux.
"""

from __future__ import annotations

import base64
import binascii
import logging
import os
import re
import stat
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from vorpal.core.errors import VorpalError

TOKEN_PREFIX = "vs1"
KEY_SIZE = 32
NONCE_SIZE = 12
_KEY_ID = re.compile(r"^[A-Za-z0-9_-]{1,32}$")
_TOKEN_PATTERN = re.compile(rf"{TOKEN_PREFIX}\.[A-Za-z0-9_-]+\.[A-Za-z0-9_=-]+")


class SecretError(VorpalError):
    """Erreur de gestion des secrets (message sans valeur sensible)."""


class MasterKeyError(SecretError):
    """Clé maître absente, mal formée ou mal protégée."""


class SecretDecryptionError(SecretError):
    """Jeton illisible : mauvaise clé, mauvais contexte ou données altérées."""


def _parse_key(entry: str) -> tuple[str, bytes]:
    key_id, sep, encoded = entry.strip().partition(":")
    if not sep or not _KEY_ID.match(key_id):
        raise MasterKeyError("Clé maître : format attendu « identifiant:base64 ».")
    try:
        key = base64.b64decode(encoded, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise MasterKeyError("Clé maître : base64 invalide.") from exc
    if len(key) != KEY_SIZE:
        raise MasterKeyError(f"Clé maître : {KEY_SIZE} octets attendus.")
    return key_id, key


def generate_master_key(key_id: str) -> str:
    """Génère une clé maître au format ``id:base64`` (à stocker hors dépôt)."""
    if not _KEY_ID.match(key_id):
        raise MasterKeyError("Identifiant de clé invalide.")
    return f"{key_id}:{base64.b64encode(os.urandom(KEY_SIZE)).decode()}"


class KeyRing:
    """Clés maîtres connues ; la clé courante chiffre, toutes peuvent déchiffrer."""

    def __init__(self, keys: Mapping[str, bytes], current: str) -> None:
        if current not in keys:
            raise MasterKeyError("La clé courante est absente du trousseau.")
        if any(len(k) != KEY_SIZE for k in keys.values()):
            raise MasterKeyError(f"Clé maître : {KEY_SIZE} octets attendus.")
        self._keys = dict(keys)
        self.current = current

    def get(self, key_id: str) -> bytes:
        try:
            return self._keys[key_id]
        except KeyError:
            raise SecretDecryptionError("Clé maître inconnue pour ce jeton.") from None

    @property
    def key_ids(self) -> tuple[str, ...]:
        return tuple(self._keys)

    @classmethod
    def from_entries(cls, entries: list[str]) -> KeyRing:
        if not entries:
            raise MasterKeyError("Aucune clé maître fournie.")
        parsed = [_parse_key(e) for e in entries]
        ids = [i for i, _ in parsed]
        if len(set(ids)) != len(ids):
            raise MasterKeyError("Identifiants de clés maîtres en double.")
        return cls(dict(parsed), current=ids[0])

    @classmethod
    def from_environment(cls, env: Mapping[str, str] | None = None) -> KeyRing:
        env = os.environ if env is None else env
        file_path = env.get("VORPAL_MASTER_KEY_FILE")
        if file_path:
            return cls.from_file(Path(file_path))
        current = env.get("VORPAL_MASTER_KEY")
        if not current:
            raise MasterKeyError("VORPAL_MASTER_KEY (ou VORPAL_MASTER_KEY_FILE) est absente.")
        previous = [p for p in env.get("VORPAL_MASTER_KEY_PREVIOUS", "").split(",") if p.strip()]
        return cls.from_entries([current, *previous])

    @classmethod
    def from_file(cls, path: Path) -> KeyRing:
        try:
            mode = path.stat().st_mode
            content = path.read_text(encoding="utf-8")
        except OSError as exc:
            raise MasterKeyError("Fichier de clés maîtres illisible.") from exc
        if os.name == "posix" and mode & (stat.S_IRWXG | stat.S_IRWXO):
            raise MasterKeyError("Fichier de clés maîtres : permissions trop larges (attendu 0600).")
        return cls.from_entries([ln for ln in content.splitlines() if ln.strip()])


class SecretBox:
    """Chiffre et déchiffre des secrets par enveloppe."""

    def __init__(self, keyring: KeyRing) -> None:
        self._keyring = keyring

    def encrypt(self, plaintext: str | bytes, context: str) -> str:
        data = plaintext.encode("utf-8") if isinstance(plaintext, str) else plaintext
        dek = AESGCM.generate_key(bit_length=256)
        data_nonce = os.urandom(NONCE_SIZE)
        ciphertext = AESGCM(dek).encrypt(data_nonce, data, _aad_data(context))
        return self._seal(dek, data_nonce, ciphertext, context, self._keyring.current)

    def decrypt(self, token: str, context: str) -> bytes:
        key_id, wrap_nonce, wrapped, data_nonce, ciphertext = _unpack(token)
        dek = self._unwrap(key_id, wrap_nonce, wrapped, context)
        try:
            return AESGCM(dek).decrypt(data_nonce, ciphertext, _aad_data(context))
        except InvalidTag:
            raise SecretDecryptionError("Jeton illisible (contexte ou données invalides).") from None

    def decrypt_text(self, token: str, context: str) -> str:
        return self.decrypt(token, context).decode("utf-8")

    def rotate(self, token: str, context: str) -> str:
        """Re-chiffre la DEK avec la clé courante ; le secret n'est pas déchiffré."""
        key_id, wrap_nonce, wrapped, data_nonce, ciphertext = _unpack(token)
        dek = self._unwrap(key_id, wrap_nonce, wrapped, context)
        return self._seal(dek, data_nonce, ciphertext, context, self._keyring.current)

    def key_id_of(self, token: str) -> str:
        return _unpack(token)[0]

    def _unwrap(self, key_id: str, nonce: bytes, wrapped: bytes, context: str) -> bytes:
        try:
            return AESGCM(self._keyring.get(key_id)).decrypt(
                nonce, wrapped, _aad_wrap(key_id, context)
            )
        except InvalidTag:
            raise SecretDecryptionError("Jeton illisible (clé ou contexte invalide).") from None

    def _seal(
        self, dek: bytes, data_nonce: bytes, ciphertext: bytes, context: str, key_id: str
    ) -> str:
        wrap_nonce = os.urandom(NONCE_SIZE)
        wrapped = AESGCM(self._keyring.get(key_id)).encrypt(
            wrap_nonce, dek, _aad_wrap(key_id, context)
        )
        blob = wrap_nonce + wrapped + data_nonce + ciphertext
        return f"{TOKEN_PREFIX}.{key_id}.{base64.urlsafe_b64encode(blob).decode()}"


def _aad_data(context: str) -> bytes:
    return b"vorpal-secret-data|" + context.encode("utf-8")


def _aad_wrap(key_id: str, context: str) -> bytes:
    return b"vorpal-secret-wrap|" + key_id.encode("utf-8") + b"|" + context.encode("utf-8")


_WRAPPED_SIZE = KEY_SIZE + 16  # DEK + étiquette GCM


def _unpack(token: str) -> tuple[str, bytes, bytes, bytes, bytes]:
    parts = token.split(".")
    if len(parts) != 3 or parts[0] != TOKEN_PREFIX or not _KEY_ID.match(parts[1]):
        raise SecretDecryptionError("Format de jeton invalide.")
    try:
        blob = base64.urlsafe_b64decode(parts[2].encode())
    except (binascii.Error, ValueError):
        raise SecretDecryptionError("Format de jeton invalide.") from None
    if len(blob) < NONCE_SIZE + _WRAPPED_SIZE + NONCE_SIZE + 16:
        raise SecretDecryptionError("Jeton tronqué.")
    wrap_nonce = blob[:NONCE_SIZE]
    wrapped = blob[NONCE_SIZE : NONCE_SIZE + _WRAPPED_SIZE]
    data_nonce = blob[NONCE_SIZE + _WRAPPED_SIZE : 2 * NONCE_SIZE + _WRAPPED_SIZE]
    return parts[1], wrap_nonce, wrapped, data_nonce, blob[2 * NONCE_SIZE + _WRAPPED_SIZE :]


@dataclass(frozen=True)
class Secret:
    """Valeur sensible en mémoire : jamais affichée par ``repr``, ``str`` ou le formatage."""

    _value: str

    def reveal(self) -> str:
        return self._value

    def __repr__(self) -> str:
        return "Secret('***')"

    def __str__(self) -> str:
        return "***"


class RedactingFilter(logging.Filter):
    """Masque les jetons chiffrés et les secrets enregistrés dans les messages de journal."""

    def __init__(self, secrets: list[Secret] | None = None) -> None:
        super().__init__()
        self._secrets = list(secrets or [])

    def register(self, secret: Secret) -> None:
        self._secrets.append(secret)

    def filter(self, record: logging.LogRecord) -> bool:
        message = record.getMessage()
        message = _TOKEN_PATTERN.sub("***", message)
        for secret in self._secrets:
            if secret.reveal():
                message = message.replace(secret.reveal(), "***")
        record.msg, record.args = message, None
        return True
