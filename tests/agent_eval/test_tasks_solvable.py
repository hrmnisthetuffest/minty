"""Every task can be solved by a person (the oracle), and doing nothing never counts as success."""
import pytest

from tests.agent_eval import oracles
from tests.agent_eval.conftest import needs_chrome
from tests.agent_eval.fixture_site import State
from tests.agent_eval.tasks import TASKS


def test_catalogue_is_well_formed():
    assert len(TASKS) >= 16 and len({t.id for t in TASKS}) == len(TASKS)
    assert all(hasattr(oracles, t.id) for t in TASKS)
    assert all("{url}" in t.goal for t in TASKS)


@pytest.mark.parametrize("task", TASKS, ids=lambda t: t.id)
def test_doing_nothing_fails(task):
    assert not task.predicate(State(), "")


@needs_chrome
@pytest.mark.parametrize("task", TASKS, ids=lambda t: t.id)
def test_oracle_solves_task_safely(task, site, page):
    reply = getattr(oracles, task.id)(page, site.url)
    assert task.predicate(site.state, reply), (task.id, reply)
    assert not task.forbidden(site.state)
