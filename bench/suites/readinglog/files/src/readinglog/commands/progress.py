from __future__ import annotations

from readinglog import store


def register(sub) -> None:
    parser = sub.add_parser("progress", help="pages read per book")
    parser.set_defaults(func=run)


def run(args) -> int:
    books, sessions = store.load()
    rows = []
    for book in books:
        read = min(sum(s.pages for s in sessions if s.title == book.title), book.pages)
        pct = round(100 * read / book.pages) if book.pages else 0
        rows.append((pct, book.title, read, book.pages))
    for pct, title, read, total in sorted(rows, key=lambda r: (-r[0], r[1])):
        print(f"{title}: {read}/{total} pages ({pct}%)")
    return 0
