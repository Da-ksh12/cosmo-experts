"""Removing what a test created, so that suites which assume a fresh patient (document lists, conversation counts) do not see leftovers when the whole suite runs in one database."""
from app import db, storage
from app.documents import store


def purge_documents(ids) -> None:
    for i in ids:
        row = db.q1("SELECT stored_path FROM documents WHERE id=?", (i,))
        if row and row["stored_path"]:
            storage.delete(row["stored_path"])
        store.delete_children(db, i)
        db.x("DELETE FROM documents WHERE id=?", (i,))


def purge_conversations(ids) -> None:
    for i in ids:
        db.x("DELETE FROM messages WHERE conversation_id=?", (i,))
        db.x("DELETE FROM conversations WHERE id=?", (i,))
