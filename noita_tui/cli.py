"""Command line entry point: open a database and show it.

Building the database is stage 1's job — `noita-db` — and this never does it,
so a missing file is an error with a hint rather than an empty database.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .db import UnusableDatabase


def make_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog='noita-tui',
        description='Browse the entity database built by noita-db.')
    parser.add_argument('database', nargs='?', default=Path('noita.db'), type=Path,
                        help='database to open (default: noita.db)')
    return parser


def main(argv: list[str] | None = None) -> int:
    args = make_parser().parse_args(argv)
    from .app import NoitaTUI       # imported late so --help works without textual

    try:
        app = NoitaTUI(args.database)
    except UnusableDatabase as error:
        print(f'noita-tui: {error}', file=sys.stderr)
        return 2
    app.run()
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
