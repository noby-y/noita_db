"""Command line entry point: read the source files, write the database.

That is the whole job. Filtering, searching and group management belong to the
GUI/TUI, which talks to the `.db` file directly.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from . import names
from .build import MissingInput, build


def make_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog='noita-db',
        description='Build a queryable SQLite database of Noita entities, '
                    'components and spells.')
    parser.add_argument(
        'root', nargs='?', default='.', type=Path,
        help='directory holding data/ and comp_docs/ (default: .)')
    parser.add_argument(
        '-o', '--output', default=Path('noita.db'), type=Path,
        help='database to write (default: noita.db)')
    parser.add_argument(
        '--preserve-groups', action='store_true',
        help='carry entity groups over from an existing database at --output')
    parser.add_argument(
        '--languages', default=','.join(names.DEFAULT_LANGUAGES),
        help='comma-separated common.csv columns to import (default: English)')
    parser.add_argument('-q', '--quiet', action='store_true', help='only report errors')
    return parser


def main(argv: list[str] | None = None) -> int:
    args = make_parser().parse_args(argv)
    log = (lambda message: None) if args.quiet else \
        (lambda message: print(message, file=sys.stderr))

    try:
        result = build(
            root=args.root,
            db_path=args.output,
            preserve_groups=args.preserve_groups,
            languages=tuple(part.strip() for part in args.languages.split(',') if part.strip()),
            log=log,
        )
    except MissingInput as error:
        print(f'noita-db: {error}', file=sys.stderr)
        return 2

    log(f'wrote {args.output}: {result.entities} entities, '
        f'{result.components} components, {result.spells} spells '
        f'({result.unimplemented} unimplemented attributes, '
        f'{result.duplicates} duplicates, {result.warnings} warnings)')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
