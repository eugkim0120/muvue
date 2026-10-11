from __future__ import annotations

from datetime import date

from readinglog import store
from readinglog.models import Session


def register(sub) -> None:
    parser = sub.add_parser("log", help="record pages read today")
    parser.add_argument("title")
    parser.add_argument("pages", type=int)
    parser.set_defaults(func=run)


def run(args) -> int:
    books, sessions = store.load()
    if not any(b.title == args.title for b in books):
        print(f"unknown book: {args.title}")
        return 1
    sessions.append(Session(args.title, date.today(), args.pages))
    store.save(books, sessions)
    print(f"logged {args.pages} pages of {args.title}")
    return 0
