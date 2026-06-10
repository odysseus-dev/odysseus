import time
import logging
from typing import Optional
from src.mdm.meta.policies import Policies
from src.mdm.meta.metrics_store import MetricsStore
from src.mdm.meta.history_store import HistoryStore

logger = logging.getLogger(__name__)


class MetaBrain:
    def __init__(self, policies: Optional[Policies] = None,
                 metrics_store: Optional[MetricsStore] = None,
                 history_store: Optional[HistoryStore] = None):
        self.policies = policies or Policies()
        self.metrics = metrics_store or MetricsStore()
        self.history = history_store or HistoryStore()

    # ── 1. Observer ──────────────────────────────────────────────

    def observe_run(self, request: dict, plan: list, steps_results: list):
        """Enregistre un run complet (plan + résultats de chaque étape)."""
        self.history.log("run", {
            "task": request.get("task", request.get("action", "")),
            "plan": plan,
            "results": steps_results,
        })
        for step, result in zip(plan, steps_results):
            self.metrics.update(step, result)

    def observe_step(self, agent: str, action: str, result: dict, duration_ms: float = 0):
        """Enregistre une étape individuelle (appelé par l'orchestrateur après dispatch)."""
        step = {"agent": agent, "action": action}
        res = {
            "status": "error" if result.get("error") else "success",
            "error": result.get("error"),
            "duration_ms": duration_ms,
        }
        self.metrics.update(step, res)
        self.history.log("run", {
            "task": f"{agent}:{action}",
            "plan": [step],
            "results": [res],
        })

    # ── 2. Analyser ──────────────────────────────────────────────

    def analyze(self) -> dict:
        stats = self.metrics.summary()
        patterns = self._find_patterns()
        return {"stats": stats, "patterns": patterns}

    # ── 3. Mettre à jour les politiques ──────────────────────────

    def update_policies(self):
        analysis = self.analyze()
        stats = analysis["stats"]
        patterns = analysis["patterns"]

        if stats["error_rate"] > self.policies.error_threshold:
            old = self.policies.max_depth
            self.policies.max_depth = max(3, self.policies.max_depth - 2)
            logger.info("MetaBrain: error_rate=%.3f > %.2f → max_depth %d→%d",
                        stats["error_rate"], self.policies.error_threshold, old, self.policies.max_depth)

        if stats.get("avg_tokens", 0) > 9000:
            old = self.policies.max_tokens
            self.policies.max_tokens = max(4000, self.policies.max_tokens - 2000)
            logger.info("MetaBrain: avg_tokens=%.0f > 9000 → max_tokens %d→%d",
                        stats["avg_tokens"], old, self.policies.max_tokens)

        for agent in patterns.get("bad_agents", []):
            self.policies.penalize_agent(agent)
            logger.info("MetaBrain: penalizing agent '%s' (penalty=%.2f)", agent,
                        self.policies.agent_rules[agent].penalty)

        for agent in patterns.get("good_agents", []):
            self.policies.reward_agent(agent)

    # ── 4. Exporter des insights ─────────────────────────────────

    def export_insights(self) -> dict:
        analysis = self.analyze()
        return {
            "summary": analysis["stats"],
            "patterns": analysis["patterns"],
            "current_policies": self.policies.describe(),
            "recent_events": self.history.get_events(limit=10),
        }

    # ── 5. Suggestion d'agent ────────────────────────────────────

    def suggest_agent(self, candidates: list[str]) -> str:
        """Retourne l'agent le moins pénalisé parmi les candidats."""
        scored = [(self.policies.agent_rules.get(a, type("r", (), {"penalty": 0})()).penalty, a) for a in candidates]
        scored.sort(key=lambda x: x[0])
        return scored[0][1]

    # ── interne ──────────────────────────────────────────────────

    def _find_patterns(self) -> dict:
        events = self.history.get_events("run", limit=200)
        bad_agents: dict[str, int] = {}
        good_agents: dict[str, int] = {}

        for e in events:
            plan = e["data"].get("plan", [])
            results = e["data"].get("results", [])
            for step, result in zip(plan, results):
                agent = step.get("agent", "?")
                if isinstance(result, dict) and result.get("status") == "error":
                    bad_agents[agent] = bad_agents.get(agent, 0) + 1
                elif isinstance(result, dict) and result.get("status") == "success":
                    good_agents[agent] = good_agents.get(agent, 0) + 1

        return {
            "bad_agents": [a for a, c in bad_agents.items() if c >= 3],
            "good_agents": [a for a, c in good_agents.items() if c >= 5 and bad_agents.get(a, 0) == 0],
        }
