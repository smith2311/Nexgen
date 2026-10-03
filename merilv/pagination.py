"""Shared pagination for every bulk list in Meril-V.

30 rows per page everywhere — materials (32k+ rows), customers (7k+), storage
locations, validation rows, activity log and the various history screens all
funnel through this one helper so the page size and the "bad page number"
behaviour never drift screen to screen.
"""

from __future__ import annotations

from django.core.paginator import Paginator

PAGE_SIZE = 30


def paginate(request, queryset, param="page", page_size=PAGE_SIZE):
    """Return a ``Page`` for ``queryset``, reading the page number from
    ``request.GET[param]``.

    ``param`` lets more than one paginated table share a single page (Master
    Data → Organisation has four) without their page numbers colliding.
    ``Paginator.get_page`` already clamps an out-of-range or non-numeric value
    to a valid page instead of raising, so a stale bookmark or a hand-edited
    URL degrades to page 1 (or the last page) rather than a 404.
    """
    paginator = Paginator(queryset, page_size)
    return paginator.get_page(request.GET.get(param))
