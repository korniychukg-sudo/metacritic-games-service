from app.db import DayState
from app.metacritic import ListItem
from app.selection import select_batch


def items(start, n, prefix="g"):
    return [ListItem(id=start + i, slug=f"{prefix}-{start + i}", title=f"Game {start + i}") for i in range(n)]


class Source:
    def __init__(self, new, pages, page_size=24):
        self.new = new
        self.pages = pages
        self.page_size = page_size
        self.browse_calls = []

    def fetch_new(self):
        return list(self.new)

    def fetch_browse(self, offset, size):
        self.browse_calls.append(offset)
        return list(self.pages.get(offset // size, []))


def run(state, processed, src, batch=20, max_pages=4):
    return select_batch(state, processed, src.fetch_new, src.fetch_browse, batch_size=batch, page_size=24,
                        max_pages=max_pages)


def test_first_run_of_day_takes_new_releases():
    src = Source(items(1, 20), {0: items(1, 24)})
    sel = run(DayState("2026-10-14"), set(), src)
    assert [i.id for i, _ in sel.items] == list(range(1, 21))
    assert {s for _, s in sel.items} == {"new-releases"}
    assert sel.state.new_releases_done is True
    assert sel.state.browse_offset == 0
    assert src.browse_calls == []


def test_second_run_walks_browse_and_skips_processed():
    src = Source(items(1, 20), {0: items(1, 24), 1: items(25, 24), 2: items(49, 24)})
    state = DayState("2026-10-14", new_releases_done=True, browse_offset=0)
    sel = run(state, set(range(1, 21)), src)
    ids = [i.id for i, _ in sel.items]
    assert ids == list(range(21, 41))
    assert sel.state.browse_offset == 24
    assert src.browse_calls == [0, 24]
    assert sel.items[0][1] == "browse:p1" and sel.items[-1][1] == "browse:p2"


def test_partly_taken_page_is_resumed_next_run():
    src = Source([], {0: items(1, 24), 1: items(25, 24)})
    state = DayState("d", new_releases_done=True)
    first = run(state, set(), src)
    assert [i.id for i, _ in first.items] == list(range(1, 21))
    assert first.state.browse_offset == 0
    processed = {i.id for i, _ in first.items}
    second = run(first.state, processed, src)
    assert [i.id for i, _ in second.items] == list(range(21, 41))
    assert second.state.browse_offset == 24


def test_new_day_starts_from_new_releases_again():
    src = Source(items(1, 20), {0: items(1, 24)})
    yesterday = DayState("2026-10-13", new_releases_done=True, browse_offset=96)
    sel = run(DayState("2026-10-14"), set(), src)
    assert sel.items[0][1] == "new-releases"
    assert yesterday.browse_offset == 96


def test_limit_smaller_than_block_keeps_block_open():
    src = Source(items(1, 20), {0: items(1, 24)})
    sel = run(DayState("d"), set(), src, batch=5)
    assert len(sel.items) == 5
    assert sel.state.new_releases_done is False
    sel2 = run(sel.state, {i.id for i, _ in sel.items}, src, batch=20)
    assert [i.id for i, _ in sel2.items] == list(range(6, 21))
    assert sel2.state.new_releases_done is True


def test_new_releases_already_processed_falls_through_to_browse():
    src = Source(items(1, 20), {0: items(1, 24)})
    sel = run(DayState("d"), set(range(1, 21)), src)
    assert sel.state.new_releases_done is True
    assert [i.id for i, _ in sel.items] == [21, 22, 23, 24]


def test_duplicates_inside_listing_are_deduped():
    dup = items(1, 3) + items(1, 3) + items(4, 2)
    src = Source(dup, {})
    sel = run(DayState("d"), set(), src)
    assert [i.id for i, _ in sel.items] == [1, 2, 3, 4, 5]


def test_page_cap_and_empty_listing():
    src = Source([], {k: items(k * 24 + 1, 24) for k in range(10)})
    state = DayState("d", new_releases_done=True)
    processed = set(range(1, 24 * 6 + 1))
    sel = run(state, processed, src, max_pages=3)
    assert sel.items == []
    assert sel.pages_fetched == 3
    assert sel.state.browse_offset == 72
    empty = run(DayState("d", True, 0), set(), Source([], {}))
    assert empty.items == [] and empty.pages_fetched == 1
