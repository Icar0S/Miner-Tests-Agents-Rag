"""Minimal `.env` loader (no dependency).

The CLI loads `.env` from the current directory before every command, so the
search parameters and credentials in it apply to native runs as they already
did in Docker (`env_file` in docker-compose.yml). Variables already present in
the environment win over the file, so a shell `export` or a CI secret is never
overwritten. Set `MSRKIT_NO_DOTENV=1` to skip loading (the test suite does).

Syntax: `KEY=value` per line; blank lines and `#` comments are ignored; an
optional `export ` prefix is accepted; single or double quotes around the value
are removed; in an unquoted value, ` #` starts a comment.
"""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import MutableMapping

_LINE = re.compile(r"^\s*(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*?)\s*$")


def parse_dotenv(text: str) -> dict[str, str]:
    values: dict[str, str] = {}
    for line in text.splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        match = _LINE.match(line)
        if not match:
            continue
        key, value = match.groups()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        else:
            value = re.split(r"\s+#", value, maxsplit=1)[0].strip()
        values[key] = value
    return values


def load_dotenv(
    path: str | Path = ".env", environ: MutableMapping[str, str] | None = None
) -> list[str]:
    """Set variables from `path` that are not already set; returns the keys set."""
    env = os.environ if environ is None else environ
    p = Path(path)
    if env.get("MSRKIT_NO_DOTENV") == "1" or not p.is_file():
        return []
    loaded = []
    for key, value in parse_dotenv(p.read_text(encoding="utf-8")).items():
        if key not in env:
            env[key] = value
            loaded.append(key)
    return loaded
