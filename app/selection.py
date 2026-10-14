import logging
from dataclasses import dataclass, field
from typing import Callable

from .db import DayState
from .metacritic import ListItem

log = logging.getLogger("selection")


@dataclass
class Selection:
    items: list[tuple[ListItem, str]] = field(default_factory=list)
    state: DayState | None = None
    pages_fetched: int = 0

    @property
    def sources(self) -> str:
        seen = []
        for _, src in self.items:
            if src not in seen:
                seen.append(src)
        return ", ".join(seen)


def select_batch(
    state: DayState,
    processed: set[int],
    fetch_new_releases: Callable[[], list[ListItem]],
    fetch_browse: Callable[[int, int], list[ListItem]],
    batch_size: int = 20,
    page_size: int = 24,
    max_pages: int = 4,
) -> Selection:
    """New Releases first, then SEE ALL pages from the cursor; the cursor passes a page only once it is fully handled."""
    state = DayState(state.day, state.new_releases_done, state.browse_offset)
    sel = Selection(state=state)
    taken: set[int] = set()

    def take(items: list[ListItem], source: str) -> bool:
        for it in items:
            if it.id in processed or it.id in taken:
                continue
            if len(sel.items) >= batch_size:
                return False
            taken.add(it.id)
            sel.items.append((it, source))
        return True

    if not state.new_releases_done:
        items = fetch_new_releases()
        complete = take(items, "new-releases")
        if complete:
            state.new_releases_done = True
        if sel.items:
            return sel

    while len(sel.items) < batch_size and sel.pages_fetched < max_pages:
        items = fetch_browse(state.browse_offset, page_size)
        sel.pages_fetched += 1
        if not items:
            break
        complete = take(items, f"browse:p{state.browse_offset // page_size + 1}")
        if not complete:
            break
        state.browse_offset += page_size
    return sel
