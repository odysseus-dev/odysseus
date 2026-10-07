"""Execute the route's production state machine without importing app services.

AST loading isolates the dependency-free class; assertions are event/content
behavior, never source text or a reimplementation of the routing logic.
Also runnable with `python tests/test_agent_render_ownership.py`.
"""
import ast
from pathlib import Path
import unittest


path = Path(__file__).resolve().parents[1] / "routes/chat_routes.py"
tree = ast.parse(path.read_text())
node = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == "_AgentRenderState")
namespace = {}
exec(compile(ast.Module(body=[node], type_ignores=[]), str(path), "exec"), namespace)
State = namespace["_AgentRenderState"]


class AgentRenderOwnershipTests(unittest.TestCase):
    def test_plain_deltas_append_and_save_streamed_owner(self):
        state = State()
        self.assertEqual(state.consume({"delta": "Hello "})["render_owner"], "streamed")
        state.consume({"delta": "world"})
        self.assertEqual(state.content, "Hello world")
        self.assertEqual(state.metadata({"tokens": 2}), {"tokens": 2, "render_owner": "streamed"})
        self.assertEqual(state.message_saved(42), {"type": "message_saved", "id": 42, "render_owner": "streamed"})

    def test_final_snapshot_replaces_draft_and_persists_ownership(self):
        state = State()
        state.consume({"delta": "Draft notes"})
        event = state.consume({"type": "final_response", "content": "Canonical notes"})
        self.assertEqual(state.content, "Canonical notes")
        self.assertEqual(event["render_owner"], "structured")
        self.assertEqual(event["replacement_scope"], "turn")
        metrics = state.metadata({"tool_events": [{"tool": "manage_notes"}]})
        self.assertEqual(metrics["render_owner"], "structured")
        self.assertEqual(metrics["replacement_scope"], "turn")
        self.assertEqual(state.message_saved(1)["render_owner"], metrics["render_owner"])
        self.assertEqual(state.message_saved(1)["replacement_scope"], "turn")

    def test_later_synthesis_replaces_intermediate_snapshot_and_is_not_dropped(self):
        state = State()
        state.consume({"type": "final_response", "content": "Intermediate list"})
        state.consume({"type": "agent_step", "round": 2})
        first = state.consume({"delta": "Synthesis "})
        second = state.consume({"delta": "continues"})
        self.assertEqual(first["render_owner"], "streamed")
        self.assertEqual(first["replacement_scope"], "turn")
        self.assertNotIn("replacement_scope", second)
        self.assertEqual(state.content, "Synthesis continues")
        self.assertEqual(state.metadata()["render_owner"], "streamed")
        self.assertEqual(state.message_saved(1)["render_owner"], "streamed")
        self.assertEqual(state.message_saved(1)["replacement_scope"], "turn")

    def test_thinking_and_empty_deltas_do_not_take_answer_ownership(self):
        state = State()
        state.consume({"type": "final_response", "content": "Notes"})
        event = state.consume({"delta": "Reasoning", "thinking": True})
        state.consume({"delta": ""})
        self.assertEqual(event["render_owner"], "structured")
        self.assertEqual(state.content, "Notes")
        self.assertEqual(state.message_saved(1)["render_owner"], "structured")

    def test_final_correction_after_synthesis_wins_without_duplicate_content(self):
        state = State()
        for event in [
            {"delta": "Draft"},
            {"type": "final_response", "content": "List"},
            {"delta": "Synthesis"},
            {"type": "final_response", "content": "Corrected answer"},
            {"type": "final_response", "content": "Corrected answer"},
        ]:
            state.consume(event)
        self.assertEqual(state.content, "Corrected answer")
        self.assertEqual(state.metadata()["render_owner"], "structured")

    def test_explicit_streamed_final_and_delta_form_snapshot(self):
        state = State()
        state.consume({"delta": "Old"})
        event = state.consume({"type": "final_response", "delta": "Replacement", "render_owner": "streamed"})
        self.assertEqual(event["replacement_scope"], "turn")
        self.assertEqual(state.content, "Replacement")
        self.assertEqual(state.metadata()["render_owner"], "streamed")


if __name__ == "__main__":
    unittest.main()
