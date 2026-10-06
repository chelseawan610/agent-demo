import json

from travel_agent.memory.store import JsonMemoryStore


def test_memory_store_round_trip_and_forget(tmp_path):
    store = JsonMemoryStore(tmp_path / "memory.json")

    saved = store.remember_preferences("u1", ["Tokyo Tower", "anime", "Tokyo Tower"])
    loaded = store.get("u1")

    assert saved.preferences == ["Tokyo Tower", "anime"]
    assert loaded.preferences == ["Tokyo Tower", "anime"]
    store.forget("u1")
    assert store.get("u1").preferences == []


def test_corrupt_or_malformed_memory_is_treated_as_empty(tmp_path):
    path = tmp_path / "memory.json"
    path.write_text('{"users": []}', encoding="utf-8")
    store = JsonMemoryStore(path)

    assert store.get("u1").preferences == []

    path.write_text("not-json", encoding="utf-8")
    assert store.get("u1").preferences == []


def test_memory_file_contains_only_bounded_preferences(tmp_path):
    path = tmp_path / "memory.json"
    store = JsonMemoryStore(path)
    store.remember_preferences("u1", [" x " + "a" * 200])

    payload = json.loads(path.read_text(encoding="utf-8"))
    assert len(payload["users"]["u1"]["preferences"][0]) == 120
    assert payload["version"] == 2
    assert payload["users"]["u1"]["source"] == "explicit_user_preference"


def test_memory_can_remove_one_preference_without_deleting_user(tmp_path):
    store = JsonMemoryStore(tmp_path / "memory.json")
    store.remember_preferences("u1", ["Tokyo Tower", "anime"])

    updated = store.forget_preferences("u1", ["anime"])

    assert updated.preferences == ["Tokyo Tower"]
    assert store.get("u1").preferences == ["Tokyo Tower"]
