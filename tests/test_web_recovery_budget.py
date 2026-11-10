from src.web_recovery import WebRecoveryBudget


def test_budget_counts_executions_and_allows_browser_as_third_option():
    budget = WebRecoveryBudget()
    assert budget.admit("web_search", "alpha beta")
    assert not budget.admit("web_search", "beta alpha")
    assert not budget.admit("private_browser")
    assert budget.admit("web_search", "alpha technical specification")
    assert not budget.admit("web_search", "yet another query")
    assert budget.admit("web_fetch")
    assert not budget.admit("web_fetch")
    assert budget.admit("private_browser")
    assert budget.admit("private_browser")
    assert budget.admit("private_browser")
    assert not budget.admit("private_browser")
    assert "exhausted" in budget.instruction()


def test_unrelated_tools_do_not_consume_web_budget():
    budget = WebRecoveryBudget()
    assert budget.admit("manage_notes")
    assert not budget.searches and not budget.fetches and not budget.browsers
