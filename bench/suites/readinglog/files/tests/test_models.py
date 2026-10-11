from datetime import date

from readinglog.models import Book, Session


def test_book_round_trips():
    book = Book("Dune", 412)
    assert Book.from_dict(book.to_dict()) == book


def test_session_round_trips_with_iso_day():
    session = Session("Dune", date(2026, 10, 11), 30)
    assert session.to_dict()["day"] == "2026-10-11"
    assert Session.from_dict(session.to_dict()) == session
