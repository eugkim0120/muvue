"""`readinglog` entry point. Subcommands are the modules in
`readinglog.commands`; each one defines `register(subparsers)`."""

from __future__ import annotations

import argparse
import importlib
import pkgutil
import sys

import readinglog.commands as commands


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="readinglog")
    sub = parser.add_subparsers(dest="command", required=True)
    for info in sorted(pkgutil.iter_modules(commands.__path__), key=lambda m: m.name):
        importlib.import_module(f"readinglog.commands.{info.name}").register(sub)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
