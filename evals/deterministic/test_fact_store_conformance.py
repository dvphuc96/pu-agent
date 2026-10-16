"""DETERMINISTIC EVAL — the local semantic-memory contract.

The product standardises on SQLite + FTS5. This suite keeps the repository
abstraction honest; any future backend must satisfy the same protocol.
"""

from __future__ import annotations

import inspect

import pytest

from pu.db import connect
from pu.memory.semantic.base import FactStore
from pu.memory.semantic.store import SqliteFactStore


@pytest.fixture
def store(tmp_path):
    return SqliteFactStore(connect(tmp_path))


def test_the_backend_declares_every_method_the_contract_requires(store):
    assert isinstance(store, FactStore), (
        f"{type(store).__name__} does not satisfy the FactStore protocol — missing: "
        + ", ".join(m for m in _protocol_methods() if not hasattr(store, m))
    )


def test_add_then_search_round_trips(store):
    store.add("jensen", "Jensen always wears a leather jacket")
    assert any("leather jacket" in hit for hit in store.search("jensen leather jacket"))


def test_a_miss_returns_empty_rather_than_raising(store):
    assert store.search("nothing here matches this at all") == []


def test_search_with_ids_returns_ids_that_update_accepts(store):
    store.add("elon", "The launch is in March")
    rows = store.search_with_ids("elon launch")
    assert rows
    assert "id" in rows[0] and "content" in rows[0]
    assert store.update(rows[0]["id"], "The launch is in June") is True


def test_update_changes_what_search_returns(store):
    store.add("elon", "The launch is in March")
    rid = store.search_with_ids("elon launch")[0]["id"]
    store.update(rid, "The launch is in June")
    assert any("June" in hit for hit in store.search("elon launch"))


def test_delete_removes_the_fact(store):
    store.add("tom", "Tom Holland cannot keep a secret")
    rid = store.search_with_ids("tom holland secret")[0]["id"]
    assert store.delete(rid) is True
    assert not any("secret" in hit for hit in store.search("tom holland secret"))


@pytest.mark.parametrize("method", ["update", "delete"])
def test_an_unknown_id_is_false_not_an_exception(store, method):
    args = (999_999, "x") if method == "update" else (999_999,)
    assert getattr(store, method)(*args) is False


def test_list_returns_what_was_added(store):
    store.add("pikachu", "Pikachu is coming to the dinner party")
    rows = store.list(50)
    assert any("Pikachu" in r["content"] for r in rows)
    assert all({"id", "subject", "content"} <= set(r) for r in rows)


def test_non_ascii_round_trips(store):
    store.add("сергей", "Сергей любит плавать")
    assert store.search("Сергей") != []


def _protocol_methods() -> list[str]:
    return [n for n, v in vars(FactStore).items() if callable(v) and not n.startswith("_")]


def test_every_method_on_the_contract_is_actually_exercised_here():
    source = inspect.getsource(inspect.getmodule(test_add_then_search_round_trips))
    body = source.split("def _protocol_methods", 1)[0]
    untested = [
        m for m in _protocol_methods()
        if f"store.{m}(" not in body and f'"{m}"' not in body
    ]
    assert untested == [], f"FactStore methods with no test in this file: {untested}"
