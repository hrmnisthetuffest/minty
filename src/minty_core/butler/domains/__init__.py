"""The built-in domains: each is a few lines of data."""
from __future__ import annotations

from minty_core.butler.domain import Domain


def all_domains() -> dict[str, Domain]:
    from minty_core.butler.domains.checklist import LEAVING
    from minty_core.butler.domains.chores import CHORES
    from minty_core.butler.domains.competitions import COMPETITIONS
    from minty_core.butler.domains.documents import DOCUMENTS
    from minty_core.butler.domains.place import PLACE
    from minty_core.butler.domains.tasks import IDEAS, TASKS
    from minty_core.butler.domains.waiting import WAITING

    return {d.name: d for d in (CHORES, TASKS, WAITING, PLACE, LEAVING, DOCUMENTS, IDEAS, COMPETITIONS)}
