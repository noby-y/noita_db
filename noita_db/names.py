"""`common.csv` — Noita's translation table.

The header names each column's language (`current_language,English,русский,...`),
and the first column holds the code name an entity's `name="$..."` attribute
points at. Only English is stored by default, but the table is keyed by
language so adding more is an insert, not a migration.
"""

from __future__ import annotations

import csv
from dataclasses import dataclass
from pathlib import Path

DEFAULT_LANGUAGES = ('English',)


@dataclass
class Translations:
    languages: list[str]
    # (code_name, language) -> text
    entries: dict[tuple[str, str], str]

    def lookup(self, key: str | None, language: str = 'English') -> str | None:
        """Resolve a `$`-prefixed name reference, e.g. `$action_light_bullet`."""
        if not key:
            return None
        return self.entries.get((strip_prefix(key), language))


def strip_prefix(key: str) -> str:
    return key[1:] if key.startswith('$') else key


def load(csv_path: Path, languages: tuple[str, ...] = DEFAULT_LANGUAGES) -> Translations:
    entries: dict[tuple[str, str], str] = {}
    with csv_path.open(encoding='utf-8-sig', newline='') as handle:
        # csv handles the quoted fields that contain commas ("some, text").
        reader = csv.reader(handle)
        header = next(reader, [])
        # Column 0 is the key column; the rest are languages, and trailing
        # unnamed columns are padding in the source file.
        columns = {name: index for index, name in enumerate(header)
                   if index > 0 and name.strip()}
        wanted = {name: index for name, index in columns.items() if name in languages}
        for row in reader:
            if not row or not row[0].strip():
                continue
            code_name = row[0].strip()
            for language, index in wanted.items():
                if index < len(row) and row[index] != '':
                    entries[(code_name, language)] = row[index]
    return Translations(languages=list(languages), entries=entries)
