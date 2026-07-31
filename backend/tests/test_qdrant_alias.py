"""Unit tests for blue/green Qdrant alias activation."""

from __future__ import annotations

from typing import Any

import pytest

from backend.app.ingestion.qdrant_alias import (
    AliasActivationError,
    activate_alias,
)


SHA = "a" * 64
TARGET = "labor_law_20260728_fd35bb1a"
ALIAS = "labor_law_active"


class FakeQdrant:
    def __init__(
        self,
        *,
        collections: set[str] | None = None,
        aliases: dict[str, str] | None = None,
        total_count: int = 833,
        fingerprint_count: int = 833,
    ) -> None:
        self.collections = collections or {TARGET, "labor_law"}
        self.aliases = aliases or {}
        self.total_count = total_count
        self.fingerprint_count = fingerprint_count
        self.writes: list[dict[str, Any]] = []

    def __call__(
        self,
        method: str,
        path: str,
        body: dict[str, Any] | None,
    ) -> dict[str, Any]:
        if method == "GET" and path == "/collections":
            return {
                "status": "ok",
                "result": {
                    "collections": [
                        {"name": name}
                        for name in sorted(self.collections)
                    ]
                },
            }
        if method == "GET" and path == "/aliases":
            return {
                "status": "ok",
                "result": {
                    "aliases": [
                        {
                            "alias_name": alias,
                            "collection_name": collection,
                        }
                        for alias, collection in self.aliases.items()
                    ]
                },
            }
        if method == "POST" and path.endswith("/points/count"):
            filtered = bool(body and body.get("filter"))
            return {
                "status": "ok",
                "result": {
                    "count": (
                        self.fingerprint_count
                        if filtered
                        else self.total_count
                    )
                },
            }
        if method == "POST" and path.startswith("/collections/aliases?"):
            assert body is not None
            self.writes.append(body)
            for action in body["actions"]:
                if "delete_alias" in action:
                    self.aliases.pop(
                        action["delete_alias"]["alias_name"],
                        None,
                    )
                if "create_alias" in action:
                    item = action["create_alias"]
                    self.aliases[item["alias_name"]] = item[
                        "collection_name"
                    ]
            return {"status": "ok", "result": True}
        raise AssertionError(f"Unexpected request: {method} {path} {body}")


def test_creates_alias_without_touching_legacy_collection() -> None:
    qdrant = FakeQdrant()

    report = activate_alias(
        request=qdrant,
        collection=TARGET,
        alias=ALIAS,
        expected_chunks=833,
        expected_sha256=SHA,
    )

    assert report["action"] == "created"
    assert qdrant.aliases[ALIAS] == TARGET
    assert "labor_law" in qdrant.collections
    assert len(qdrant.writes) == 1


def test_switch_is_one_atomic_alias_update() -> None:
    qdrant = FakeQdrant(
        aliases={ALIAS: "labor_law_previous"},
    )

    report = activate_alias(
        request=qdrant,
        collection=TARGET,
        alias=ALIAS,
        expected_chunks=833,
        expected_sha256=SHA,
    )

    assert report["action"] == "switched"
    assert report["previous_collection"] == "labor_law_previous"
    assert len(qdrant.writes) == 1
    assert len(qdrant.writes[0]["actions"]) == 2
    assert qdrant.aliases[ALIAS] == TARGET


def test_idempotent_when_alias_is_already_active() -> None:
    qdrant = FakeQdrant(aliases={ALIAS: TARGET})

    report = activate_alias(
        request=qdrant,
        collection=TARGET,
        alias=ALIAS,
        expected_chunks=833,
        expected_sha256=SHA,
    )

    assert report["action"] == "already_active"
    assert qdrant.writes == []


def test_refuses_wrong_point_count_before_alias_write() -> None:
    qdrant = FakeQdrant(total_count=832)

    with pytest.raises(AliasActivationError, match="has 832 points"):
        activate_alias(
            request=qdrant,
            collection=TARGET,
            alias=ALIAS,
            expected_chunks=833,
            expected_sha256=SHA,
        )

    assert qdrant.writes == []


def test_refuses_partial_fingerprint_before_alias_write() -> None:
    qdrant = FakeQdrant(fingerprint_count=832)

    with pytest.raises(AliasActivationError, match="only 832/833"):
        activate_alias(
            request=qdrant,
            collection=TARGET,
            alias=ALIAS,
            expected_chunks=833,
            expected_sha256=SHA,
        )

    assert qdrant.writes == []


def test_refuses_alias_collision_with_physical_collection() -> None:
    qdrant = FakeQdrant(
        collections={TARGET, "labor_law", ALIAS},
    )

    with pytest.raises(AliasActivationError, match="collides"):
        activate_alias(
            request=qdrant,
            collection=TARGET,
            alias=ALIAS,
            expected_chunks=833,
            expected_sha256=SHA,
        )


def test_verify_only_never_writes() -> None:
    qdrant = FakeQdrant(aliases={ALIAS: TARGET})

    report = activate_alias(
        request=qdrant,
        collection=TARGET,
        alias=ALIAS,
        expected_chunks=833,
        expected_sha256=SHA,
        verify_only=True,
    )

    assert report["status"] == "verified"
    assert report["action"] == "verified"
    assert qdrant.writes == []
