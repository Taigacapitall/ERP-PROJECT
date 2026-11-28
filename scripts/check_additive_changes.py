#!/usr/bin/env python3
"""Refuse toute ligne supprimée ou modifiée dans des fichiers existants (ADR-003, règle R3).

Usage : python scripts/check_additive_changes.py [--base core-v1] [chemin ...]
Par défaut : base ``core-v1``, chemin ``vorpal/core``. Code de sortie : 0 conforme, 1 lignes
supprimées, 2 erreur (base introuvable, git indisponible). Les renommages et suppressions de
fichiers comptent comme des lignes supprimées. Les fichiers binaires modifiés sont refusés.
"""

from __future__ import annotations

import argparse
import subprocess
import sys


def removed_lines(base: str, paths: list[str]) -> list[tuple[str, str]]:
    """Retourne (fichier, nombre de lignes supprimées ou « binaire ») pour chaque non-conformité."""
    out = subprocess.run(
        ["git", "diff", "--numstat", "--no-renames", base, "--", *paths],
        capture_output=True, text=True, check=False,
    )
    if out.returncode != 0:
        raise RuntimeError(out.stderr.strip() or f"git diff a échoué (base {base})")
    problems: list[tuple[str, str]] = []
    for row in out.stdout.splitlines():
        added, deleted, name = row.split("\t", 2)
        if added == "-" or deleted == "-":
            problems.append((name, "binaire"))
        elif int(deleted) > 0:
            problems.append((name, deleted))
    return problems


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base", default="core-v1", help="référence Git de comparaison")
    parser.add_argument("paths", nargs="*", default=["vorpal/core"])
    args = parser.parse_args(argv)
    try:
        problems = removed_lines(args.base, args.paths)
    except (RuntimeError, OSError) as exc:
        print(f"ERREUR : {exc}", file=sys.stderr)
        return 2
    for name, count in problems:
        print(f"NON ADDITIF : {name} ({count})", file=sys.stderr)
    if problems:
        return 1
    print(f"OK : changements additifs par rapport à {args.base} ({', '.join(args.paths)})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
