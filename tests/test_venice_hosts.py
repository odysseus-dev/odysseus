"""Venice host-allowlist behavior (follow-up to provider support).

Venice (https://api.venice.ai/api/v1) is a paid, OpenAI-compatible cloud API
with native tool-calling. These tests pin the three host-list integrations:
  - agent loop sends native tool schemas to Venice (not fenced-block parsing),
  - teacher escalation treats Venice as SOTA (loop OFF, no added latency).
"""
from src import agent_loop, teacher_escalation


class TestAgentToolHosts:
    def test_venice_in_api_hosts(self):
        assert "api.venice.ai" in agent_loop._API_HOSTS

    def test_venice_url_matches_api_host(self):
        # Mirrors the runtime check: any(h in endpoint_url for h in _API_HOSTS)
        url = "https://api.venice.ai/api/v1/chat/completions"
        assert any(h in url for h in agent_loop._API_HOSTS)

    def test_unknown_host_not_matched(self):
        url = "https://example.invalid/v1/chat/completions"
        assert not any(h in url for h in agent_loop._API_HOSTS)


class TestTeacherEscalationSota:
    def test_venice_is_sota_not_self_hosted(self):
        assert teacher_escalation.is_self_hosted("https://api.venice.ai/api/v1/chat/completions") is False

    def test_known_cloud_still_sota(self):
        assert teacher_escalation.is_self_hosted("https://api.openai.com/v1") is False

    def test_local_endpoint_still_self_hosted(self):
        assert teacher_escalation.is_self_hosted("http://localhost:8000/v1") is True
