import json
import threading
from concurrent.futures import ThreadPoolExecutor


def test_urgency_state_transaction_serializes_decision_and_checkpoint(tmp_path):
    """A later worker must observe the first worker's delivered UID."""
    from src.builtin_actions import _run_email_urgency_state_transaction

    state_path = tmp_path / "email_urgency_state_alice.json"
    lock_db = tmp_path / "scheduled-emails.db"
    first_entered = threading.Event()
    allow_first_to_finish = threading.Event()
    second_entered = threading.Event()
    deliveries = []

    def operation(name):
        def update(prior):
            if name == "first":
                first_entered.set()
                assert allow_first_to_finish.wait(timeout=2)
            else:
                second_entered.set()

            notified = set(prior.get("notified_uids", []))
            delivered = "acct:42" not in notified
            if delivered:
                deliveries.append(name)
                notified.add("acct:42")
            return delivered, {
                "owner": "alice",
                "notified_uids": sorted(notified),
            }

        return _run_email_urgency_state_transaction(
            state_path,
            lock_db,
            update,
        )

    with ThreadPoolExecutor(max_workers=2) as executor:
        first = executor.submit(operation, "first")
        assert first_entered.wait(timeout=2)
        second = executor.submit(operation, "second")
        assert not second_entered.wait(timeout=0.2)
        allow_first_to_finish.set()

        assert first.result(timeout=2) is True
        assert second.result(timeout=2) is False

    assert deliveries == ["first"]
    assert json.loads(state_path.read_text(encoding="utf-8")) == {
        "owner": "alice",
        "notified_uids": ["acct:42"],
    }

