"""Tests de vorpal.plateforme.security.secrets (brique B2)."""

from __future__ import annotations

import logging
import os
import stat
from pathlib import Path

import pytest

from vorpal.plateforme.security.secrets import (
    KeyRing,
    MasterKeyError,
    RedactingFilter,
    Secret,
    SecretBox,
    SecretDecryptionError,
    generate_master_key,
)

CTX = "entreprise:42"


def make_box(*entries: str) -> SecretBox:
    return SecretBox(KeyRing.from_entries(list(entries)))


def test_round_trip_text_and_bytes() -> None:
    box = make_box(generate_master_key("k1"))
    token = box.encrypt("mot-de-passe-base", CTX)
    assert box.decrypt_text(token, CTX) == "mot-de-passe-base"
    assert box.decrypt(box.encrypt(b"\x00\xff", CTX), CTX) == b"\x00\xff"


def test_token_never_contains_plaintext_and_is_randomized() -> None:
    box = make_box(generate_master_key("k1"))
    a, b = box.encrypt("secret-xyz", CTX), box.encrypt("secret-xyz", CTX)
    assert "secret-xyz" not in a and a != b


def test_wrong_master_key_is_refused() -> None:
    token = make_box(generate_master_key("k1")).encrypt("s", CTX)
    with pytest.raises(SecretDecryptionError):
        make_box(generate_master_key("k1")).decrypt(token, CTX)  # même id, autre clé
    with pytest.raises(SecretDecryptionError):
        make_box(generate_master_key("autre")).decrypt(token, CTX)  # id inconnu


def test_wrong_context_is_refused() -> None:
    box = make_box(generate_master_key("k1"))
    with pytest.raises(SecretDecryptionError):
        box.decrypt(box.encrypt("s", CTX), "entreprise:43")


def test_tampered_or_malformed_tokens_are_refused() -> None:
    box = make_box(generate_master_key("k1"))
    token = box.encrypt("s", CTX)
    flipped = token[:-3] + ("AAA" if not token.endswith("AAA") else "BBB")
    for bad in (flipped, token[:-10], "n'importe quoi", "vs1.k1.%%%", "vs2.k1." + token.split(".")[2]):
        with pytest.raises(SecretDecryptionError):
            box.decrypt(bad, CTX)


def test_rotation_without_loss() -> None:
    old, new = generate_master_key("k1"), generate_master_key("k2")
    token = make_box(old).encrypt("valeur", CTX)
    rotating = make_box(new, old)  # k2 courante, k1 conservée pour lire l'ancien jeton
    assert rotating.key_id_of(token) == "k1"
    rotated = rotating.rotate(token, CTX)
    assert rotating.key_id_of(rotated) == "k2" and rotated != token
    assert rotating.decrypt_text(token, CTX) == "valeur"  # ancien jeton encore lisible
    assert make_box(new).decrypt_text(rotated, CTX) == "valeur"  # k1 peut être retirée
    with pytest.raises(SecretDecryptionError):
        make_box(new).decrypt(token, CTX)


def test_master_key_format_validation() -> None:
    for bad in ("", "sans-deux-points", "k1:pas base64!", "k1:AAAA", "bad id:" + generate_master_key("x")[2:]):
        with pytest.raises(MasterKeyError):
            KeyRing.from_entries([bad])
    with pytest.raises(MasterKeyError):
        KeyRing.from_entries([])
    k = generate_master_key("k1")
    with pytest.raises(MasterKeyError):
        KeyRing.from_entries([k, k])


def test_from_environment_variables_and_absence() -> None:
    k1, k2 = generate_master_key("k1"), generate_master_key("k2")
    ring = KeyRing.from_environment({"VORPAL_MASTER_KEY": k2, "VORPAL_MASTER_KEY_PREVIOUS": k1})
    assert ring.current == "k2" and set(ring.key_ids) == {"k1", "k2"}
    with pytest.raises(MasterKeyError):
        KeyRing.from_environment({})


@pytest.mark.skipif(os.name != "posix", reason="permissions POSIX")
def test_key_file_requires_protected_permissions(tmp_path: Path) -> None:
    f = tmp_path / "master.key"
    f.write_text(generate_master_key("k1") + "\n")
    f.chmod(0o644)
    with pytest.raises(MasterKeyError):
        KeyRing.from_environment({"VORPAL_MASTER_KEY_FILE": str(f)})
    f.chmod(stat.S_IRUSR | stat.S_IWUSR)
    assert KeyRing.from_environment({"VORPAL_MASTER_KEY_FILE": str(f)}).current == "k1"
    with pytest.raises(MasterKeyError):
        KeyRing.from_environment({"VORPAL_MASTER_KEY_FILE": str(tmp_path / "absent")})


def test_secret_wrapper_never_prints_value() -> None:
    s = Secret("hunter2-valeur")
    assert "hunter2" not in repr(s) and "hunter2" not in str(s) and "hunter2" not in f"{s!r} {s}"
    assert s.reveal() == "hunter2-valeur"


def test_secrets_are_absent_from_logs(caplog: pytest.LogCaptureFixture) -> None:
    box = make_box(generate_master_key("k1"))
    token = box.encrypt("clair-123", CTX)
    secret = Secret("clair-123")
    logger = logging.getLogger("test_secrets_logs")
    logger.addFilter(RedactingFilter([secret]))
    with caplog.at_level(logging.INFO, logger="test_secrets_logs"):
        logger.info("jeton %s valeur %s", token, "clair-123")
    assert token not in caplog.text and "clair-123" not in caplog.text and "***" in caplog.text


def test_errors_do_not_leak_plaintext_or_keys() -> None:
    key = generate_master_key("k1")
    box = make_box(key)
    with pytest.raises(SecretDecryptionError) as info:
        box.decrypt(box.encrypt("clair-123", CTX), "autre")
    assert "clair-123" not in str(info.value) and key.split(":")[1] not in str(info.value)
