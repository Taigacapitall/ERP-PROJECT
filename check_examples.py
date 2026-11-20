"""Exécute chaque bloc python de docs/CORE_DOCUMENTATION.md dans un processus isolé.

Usage : python docs/check_examples.py   (depuis la racine du dépôt, paquet installé)
"""
import re
import subprocess
import sys
from pathlib import Path

DOC = Path(__file__).with_name("CORE_DOCUMENTATION.md")
FENCE = "`" * 3
BLOCKS = re.findall(FENCE + r"python\n(.*?)" + FENCE, DOC.read_text(encoding="utf-8"), re.DOTALL)


def main() -> int:
    failures = 0
    for number, code in enumerate(BLOCKS, start=1):
        result = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, check=False)
        status = "OK" if result.returncode == 0 else "ECHEC"
        print(f"[{status}] exemple {number}")
        if result.returncode != 0:
            failures += 1
            print(result.stderr)
    print(f"{len(BLOCKS) - failures}/{len(BLOCKS)} exemples valides")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
