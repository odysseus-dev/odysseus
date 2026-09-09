from __future__ import annotations

from tests.agents._import_agents import ensure_agents_package

ensure_agents_package()

from services.agents.dispatcher import AgentDispatcher  # noqa: E402
from services.agents.scheduling import AutomationScheduler  # noqa: E402


class FakeClient:
    def __init__(self) -> None:
        self.n = 0

    def create_or_resume(self, **kwargs):
        self.n += 1
        conversation_id = f"conv-sched-{self.n}"
        return type(
            "Resume",
            (),
            {"conversation_id": conversation_id, "execution_id": f"agent-server:{conversation_id}"},
        )()


def test_each_schedule_fire_resolves_current_policy():
    scheduler = AutomationScheduler(dispatcher=AgentDispatcher(client=FakeClient()))
    first = scheduler.fire("schedule-1")
    scheduler.set_policy_revision(8)
    second = scheduler.fire("schedule-1")
    assert first.execution_id != second.execution_id
    assert first.token_id != second.token_id
    assert second.policy_revision == 8


def test_schedule_persists_intent_only_not_credentials():
    scheduler = AutomationScheduler(dispatcher=AgentDispatcher(client=FakeClient()))
    fired = scheduler.fire("schedule-1")
    assert not hasattr(scheduler, "stored_token")
    assert fired.schedule_id == "schedule-1"
