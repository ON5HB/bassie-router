#!/bin/bash
# Nieuwe versie van Bassie-Router vastleggen in de source.
# Gebruik: ./bump-version.sh 0.1.2 "korte omschrijving van de wijziging"
# Er wordt geen zip gebouwd: de router haalt updates met 'git pull'.
set -euo pipefail

NEW=${1:?gebruik: ./bump-version.sh 0.1.2 "omschrijving"}
MSG=${2:-"Wijzigingen"}

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT"

python3 - "$NEW" "$MSG" <<'PY'
import re
import sys
from datetime import date
from pathlib import Path

new, msg = sys.argv[1], sys.argv[2]

Path("VERSION").write_text(new + "\n", encoding="utf-8")

path = Path("pyproject.toml")
text = path.read_text(encoding="utf-8")
path.write_text(
    re.sub(r'(?m)^version = "[^"]+"', f'version = "{new}"', text, count=1),
    encoding="utf-8",
)

path = Path("src/bassierouter/__init__.py")
text = path.read_text(encoding="utf-8")
text = re.sub(r'__version__ = "[^"]+"', f'__version__ = "{new}"', text, count=1)
text = re.sub(
    r'__release_date__ = "[^"]+"',
    f'__release_date__ = "{date.today().isoformat()}"',
    text,
    count=1,
)
path.write_text(text, encoding="utf-8")

path = Path("README.md")
text = path.read_text(encoding="utf-8")
text = re.sub(r"bassie-router-v\d+\.\d+\.\d+\.zip", f"bassie-router-v{new}.zip", text)
if re.search(r"(?m)^Versie: ", text):
    text = re.sub(r"(?m)^Versie: .*$", f"Versie: {new}", text, count=1)
else:
    text = text.replace("# Bassie-Router\n", f"# Bassie-Router\n\nVersie: {new}\n", 1)
path.write_text(text, encoding="utf-8")

changelog = Path("CHANGELOG.md")
old = changelog.read_text(encoding="utf-8") if changelog.exists() else "# Changelog\n"
head, _, rest = old.partition("\n")
entry = f"## {new} — {date.today().isoformat()}\n\n- {msg}\n\n"
changelog.write_text(f"{head}\n\n{entry}{rest.lstrip(chr(10))}", encoding="utf-8")
PY

echo "Versie ${NEW} vastgelegd in VERSION, pyproject.toml, __init__.py, README.md en CHANGELOG.md"
echo "Geen zip: push de source en doe op de router 'git pull' + 'bash install.sh'."
