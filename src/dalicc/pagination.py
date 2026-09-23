# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2021-2026 DALICC - Verein zur Foerderung der Rechtssicherheit in der Datenbewirtschaftung (ZVR 1249185710)
"""Walking the two endpoints that page with ``skip`` and ``limit``.

The DALICC API pages in one way only: an offset and a page size. There is no cursor
and no ``next`` link, so paging is a loop that stops when a page comes back shorter
than it asked for. :func:`paginate` is that loop, written once::

    from dalicc.pagination import paginate

    for row in paginate(lambda skip, limit: api.licenses.history(skip, limit)["records"]):
        print(row["id"])

The client uses it for ``licenses.iter_list()`` and ``licenses.iter_history()``, and
it is exported because the same shape turns up on endpoints the SDK does not wrap.

One warning about ``GET /licenselibrary/list``: when the client sends neither ``skip``
nor ``limit`` the service answers with **every** row, which is the frozen behaviour of
that operation. Paging it is therefore a way to be gentle with a slow connection, not
a way to get more rows than one call would give.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Iterator
from typing import Any, TypeVar

__all__ = ["paginate"]

Item = TypeVar("Item")


def paginate(
    fetch: Callable[[int, int], Iterable[Item]],
    *,
    skip: int = 0,
    limit: int = 100,
    max_items: int | None = None,
) -> Iterator[Item]:
    """Yield every item of a ``skip``/``limit`` endpoint, one page at a time.

    ``fetch(skip, limit)`` is called until it returns fewer items than ``limit`` (the
    last page) or nothing at all. ``max_items`` stops earlier; ``limit`` is the page
    size, not a total.

    A page that is not shorter than ``limit`` but repeats the previous offset would
    loop forever, so the offset is always advanced by the size of the page that came
    back and a page of zero items ends the walk.
    """
    if limit < 1:
        raise ValueError("limit must be at least 1")
    if skip < 0:
        raise ValueError("skip cannot be negative")

    seen = 0
    offset = skip
    while True:
        page: list[Any] = list(fetch(offset, limit))
        if not page:
            return
        for item in page:
            yield item
            seen += 1
            if max_items is not None and seen >= max_items:
                return
        if len(page) < limit:
            return
        offset += len(page)
