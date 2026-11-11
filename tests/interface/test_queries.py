"""Scalar projection/query semantics; synthetic rows are not solver evidence."""

from contextlib import contextmanager
from types import SimpleNamespace

import pandas as pd
import pytest

pytest.importorskip("fastapi")
from pydantic import ValidationError

from tsp_hpc.interface.contracts import TrialRow
from tsp_hpc.interface.errors import InterfaceError
from tsp_hpc.interface.queries import TrialQueries
from tsp_hpc.interface.server import TrialQueryRequest


class Snapshot:
    def __init__(self, frame):
        self.frame = frame

    @contextmanager
    def analysis_snapshot(self, dataset_id, revision):
        yield SimpleNamespace(trials=self.frame)


def request(**changes):
    return TrialQueryRequest.model_validate({
        "api_version": 1, "request_id": "query", "workspace_id": "workspace",
        "dataset_id": "dataset", "revision": "revision", "filters": [],
        "sort": None, "limit": 100, "cursor": None, **changes,
    }).model_dump()


@pytest.fixture
def queries():
    rows = []
    for attempt, implementation, cost, cities in [("d", None, None, 10), ("b", "a", 10, 10),
                                                  ("c", "Z", 2, 2), ("a", "ä", 2, 2), ("e", "a", 0, 2)]:
        rows.append({"attempt_id": attempt, "instance_id": "instance", "implementation": implementation,
                     "n_cities": cities, "algorithm": "nn", "outcome": "failed" if cost is None else "completed",
                     "best_cost": cost, "search_time_s": cost, "campaign_complete": attempt != "c",
                     "duplicate_execution": attempt == "a", "usable": attempt in ("b", "e"),
                     "private_extra": "/not/for/the/wire"})
    return TrialQueries(Snapshot(pd.DataFrame(rows)))


def ids(page):
    return [row["attempt_id"] for row in page["items"]]


@pytest.mark.parametrize("sort,expected", [
    (None, ["a", "b", "c", "d", "e"]),
    ({"field": "best_cost", "direction": "ascending"}, ["e", "a", "c", "b", "d"]),
    ({"field": "best_cost", "direction": "descending"}, ["b", "a", "c", "e", "d"]),
    ({"field": "implementation", "direction": "ascending"}, ["c", "b", "e", "a", "d"]),
    ({"field": "n_cities", "direction": "ascending"}, ["a", "c", "e", "b", "d"]),
])
def test_typed_order_nulls_last_and_ascending_attempt_ties(queries, sort, expected):
    before = queries.datasets.frame.copy(deep=True)
    page = queries.page(request(sort=sort))
    assert ids(page) == expected
    assert page["next_cursor"] is None
    assert all(row.keys() == TrialRow.__required_keys__ for row in page["items"])
    assert next(row for row in page["items"] if row["attempt_id"] == "d")["best_cost"] is None
    assert next(row for row in page["items"] if row["attempt_id"] == "e")["best_cost"] == 0
    pd.testing.assert_frame_equal(before, queries.datasets.frame)


def test_filters_or_within_and_across_columns_preserve_exclusions(queries):
    page = queries.page(request(filters=[{"field": "best_cost", "values": [0, 2, 2]},
                                         {"field": "n_cities", "values": [2]},
                                         {"field": "outcome", "values": []}]))
    assert ids(page) == ["a", "c", "e"]
    assert page["items"][0]["duplicate_execution"] and not page["items"][0]["usable"]
    assert not page["items"][1]["campaign_complete"] and not page["items"][1]["usable"]
    assert ids(queries.page(request(filters=[{"field": "implementation", "values": [None]}]))) == ["d"]
    assert ids(queries.page(request(filters=[{"field": "implementation", "values": ["A"]}]))) == []
    assert ids(queries.page(request(filters=[{"field": "outcome", "values": ["failed"]}]))) == ["d"]


def test_pagination_normalization_and_empty_results(queries):
    first = queries.page(request(limit=2))
    second = queries.page(request(limit=2, cursor=first["next_cursor"], request_id="next"))
    last = queries.page(request(limit=2, cursor=second["next_cursor"]))
    assert ids(first) + ids(second) + ids(last) == ["a", "b", "c", "d", "e"]
    assert last["next_cursor"] is None and second["request_id"] == "next"
    empty = queries.page(request(filters=[{"field": "algorithm", "values": ["sa"]}]))
    assert empty["items"] == [] and empty["next_cursor"] is None
    a = request(filters=[{"field": "best_cost", "values": [2, 0, None, 2]},
                         {"field": "algorithm", "values": ["nn"]}])
    b = request(filters=[{"field": "algorithm", "values": ["nn", "nn"]},
                         {"field": "best_cost", "values": [None, -0.0, 2.0]},
                         {"field": "outcome", "values": []}],
                sort={"field": "attempt_id", "direction": "ascending"}, request_id="different", limit=1)
    assert queries.page(a)["query_key"] == queries.page(b)["query_key"]
    queries.datasets.frame = queries.datasets.frame.iloc[:0]
    assert queries.page(request())["items"] == []


@pytest.mark.parametrize("change", [
    {"limit": 3}, {"dataset_id": "other"}, {"workspace_id": "other"}, {"revision": "other"},
    {"filters": [{"field": "algorithm", "values": ["nn"]}]},
    {"sort": {"field": "best_cost", "direction": "descending"}},
])
def test_cursor_cannot_cross_context_or_query(queries, change):
    cursor = queries.page(request(limit=2))["next_cursor"]
    with pytest.raises(InterfaceError) as error:
        queries.page(request(**{"limit": 2, "cursor": cursor, **change}))
    assert error.value.code == "cursor_invalid"


def test_cursor_tampering_and_other_session_are_rejected(queries):
    cursor = queries.page(request(limit=2))["next_cursor"]
    for bad in ("!", cursor[1:], "A" + cursor[1:]):
        with pytest.raises(InterfaceError) as error:
            queries.page(request(limit=2, cursor=bad))
        assert error.value.code == "cursor_invalid"
    other = TrialQueries(queries.datasets)
    with pytest.raises(InterfaceError) as error:
        other.page(request(limit=2, cursor=cursor))
    assert error.value.code == "cursor_invalid"


@pytest.mark.parametrize("change", [
    {"limit": True}, {"limit": 0}, {"limit": 501}, {"limit": 1.5}, {"cursor": "x" * 513},
    {"filters": [{"field": "unknown", "values": []}]},
    {"filters": [{"field": "algorithm", "values": []}] * 2},
    {"filters": [{"field": "algorithm", "values": ["nn"] * 501}]},
    {"filters": [{"field": "best_cost", "values": [True]}]},
    {"filters": [{"field": "best_cost", "values": [float("inf")]}]},
    {"filters": [{"field": "best_cost", "values": [float("nan")]}]},
    {"filters": [{"field": "best_cost", "values": [10**1000]}]},
    {"filters": [{"field": "best_cost", "values": ["0"]}]},
    {"filters": [{"field": "n_cities", "values": [2.0]}]},
    {"filters": [{"field": "n_cities", "values": [2**53]}]},
    {"filters": [{"field": "algorithm", "values": [None]}]},
    {"filters": [{"field": "algorithm", "values": [1]}]},
    {"sort": {"field": "attempt_id", "direction": "invalid"}},
    {"sort": {"field": "attempt_id", "direction": "ascending", "extra": 1}},
])
def test_strict_request_types_and_bounds(change):
    with pytest.raises(ValidationError):
        request(**change)


def test_unsafe_count_metadata_is_not_rounded_for_wire(queries):
    queries.datasets.frame.loc[0, "n_cities"] = 2**53
    with pytest.raises(InterfaceError) as error:
        queries.page(request())
    assert error.value.code == "invalid_artifact"
