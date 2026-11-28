"""Tests du contrat de pagination (Core v1.1)."""

import pytest
from pydantic import ValidationError

from vorpal.core.contracts import (
    DEFAULT_PAGE_SIZE,
    MAX_PAGE_SIZE,
    Page,
    PageRequest,
    SortSpec,
)


def test_page_request_defaults() -> None:
    r = PageRequest()
    assert (r.page, r.page_size, r.sort, r.offset) == (1, DEFAULT_PAGE_SIZE, (), 0)


def test_page_request_offset() -> None:
    assert PageRequest(page=3, page_size=20).offset == 40


@pytest.mark.parametrize(
    "kwargs",
    [{"page": 0}, {"page_size": 0}, {"page_size": MAX_PAGE_SIZE + 1}, {"page": -1}],
)
def test_page_request_rejects_invalid_values(kwargs: dict[str, int]) -> None:
    with pytest.raises(ValidationError):
        PageRequest.model_validate(kwargs)


def test_page_request_accepts_max_page_size() -> None:
    assert PageRequest(page_size=MAX_PAGE_SIZE).page_size == MAX_PAGE_SIZE


def test_sort_spec_default_direction_and_validation() -> None:
    assert SortSpec(field="created_at").direction == "asc"
    assert SortSpec(field="total", direction="desc").direction == "desc"
    for bad in ("", "a b", "1abc", "x;drop", "a" * 65):
        with pytest.raises(ValidationError):
            SortSpec(field=bad)
    with pytest.raises(ValidationError):
        SortSpec.model_validate({"field": "x", "direction": "up"})


def test_models_are_immutable() -> None:
    with pytest.raises(ValidationError):
        PageRequest().__setattr__("page", 2)


def test_page_computed_values() -> None:
    req = PageRequest(page=2, page_size=10)
    page = Page[int].of(list(range(10)), total=25, request=req)
    assert (page.pages, page.has_next, page.page, page.page_size) == (3, True, 2, 10)
    assert Page[int].of([1], total=21, request=PageRequest(page=3, page_size=10)).has_next is False


def test_empty_page() -> None:
    page = Page[str].of([], total=0, request=PageRequest())
    assert (page.items, page.pages, page.has_next) == ([], 0, False)


def test_page_rejects_negative_total() -> None:
    with pytest.raises(ValidationError):
        Page[int](items=[], total=-1, page=1, page_size=10)


def test_page_validates_item_type() -> None:
    with pytest.raises(ValidationError):
        Page[int].model_validate({"items": ["x"], "total": 1, "page": 1, "page_size": 10})


def test_page_serializes_round_trip() -> None:
    page = Page[int].of([1, 2], total=2, request=PageRequest(page_size=2))
    assert Page[int].model_validate(page.model_dump()) == page
