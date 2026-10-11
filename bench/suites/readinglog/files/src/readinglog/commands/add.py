from __future__ import annotations

from readinglog import store
from readinglog.models import Book


def register(sub) -> None:
    parser = sub.add_parser("add", help="add a book")
    parser.add_argument("title")
    parser.add_argument("--pages", type=int, required=True)
    parser.set_defaults(func=run)


def run(args) -> int:
    books, sessions = store.load()
    if any(b.title == args.title for b in books):
        print(f"already in the log: {args.title}")
        return 1
    books.append(Book(args.title, args.pages))
    store.save(books, sessions)
    print(f"added {args.title} ({args.pages} pages)")
    return 0
