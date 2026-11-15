"""DR-5.7 tests for domain-guided follow-up query generation."""
import subprocess
import sys
from unittest.mock import AsyncMock, patch

import pytest

from src.deep_research import DeepResearcher
from src.research_verifier import verify_report_evidence
from src.verifiers.base import ClaimAssessment, VerifierRegistry


class _StubDomainPlugin:
    def __init__(self, name: str, domain: str, assessments):
        self.name = name
        self.domain = domain
        self.domains = (domain,)
        self._assessments = assessments

    def supports_category(self, category: str) -> bool:
        return True

    def verify(self, report_text, assessments, edges, provenance_summary, baseline_report):
        return list(self._assessments)


def _ca(claim_text: str, domain: str, verdict: str) -> ClaimAssessment:
    return ClaimAssessment(
        claim_text=claim_text,
        domain=domain,
        claim_assessment=verdict,
        publication_status="UNVERIFIED",
        legal_currency="CURRENT",
        source_dependence="INDEPENDENT",
        evidence_classification="CONTESTED_CONFLICTING",
        authority_scope_note="Domain verification check.",
    )


def test_R1_disabled_mode_produces_zero_domain_followup_queries_and_preserves_lazy_imports():
    code = (
        "import sys; "
        "from src.research_verifier import verify_report_evidence; "
        "vr = verify_report_evidence('Revenue reached 10B in Q4.', [{'url': 'https://example.com', 'text': 'Revenue reached 10B in Q4.'}], enable_domain_verifiers=False); "
        "assert vr.followup_queries == []; "
        "assert 'src.verifiers.base' not in sys.modules"
    )
    proc = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, check=False)
    assert proc.returncode == 0, proc.stderr


@pytest.mark.parametrize(
    "domain,expected_suffix",
    [
        ("financial", "SEC filing 10-K 10-Q official financial report"),
        ("legal", "statute effective date court ruling official text"),
        ("medical", "peer reviewed randomized controlled trial clinical guideline"),
        ("technical", "official specification documentation benchmark"),
        ("general", "primary source official verification"),
    ],
)
def test_R2_to_R5_domain_templates_for_contested_and_insufficient_claims(domain, expected_suffix):
    reg = VerifierRegistry()
    reg.register(
        _StubDomainPlugin(
            f"stub_{domain}",
            domain,
            [_ca("Acme Corp Q4 operating margin expanded to 28 percent", domain, "CONTESTED")],
        )
    )
    vr = verify_report_evidence(
        "Acme Corp Q4 operating margin expanded to 28 percent.",
        [{"url": "https://example.com/a", "text": "Acme Corp Q4 operating margin expanded to 28 percent."}],
        enable_domain_verifiers=True,
        category=domain,
        registry=reg,
    )
    assert len(vr.followup_queries) == 1
    assert expected_suffix in vr.followup_queries[0]
    assert "Acme Corp Q4 operating margin expanded to 28 percent" in vr.followup_queries[0]


def test_R6_supported_assessments_do_not_trigger_followup_queries():
    reg = VerifierRegistry()
    reg.register(
        _StubDomainPlugin(
            "stub_fin",
            "financial",
            [_ca("Treasury 10Y benchmark yield was 4.25 percent", "financial", "SUPPORTED")],
        )
    )
    vr = verify_report_evidence(
        "Treasury 10Y benchmark yield was 4.25 percent.",
        [{"url": "https://treasury.gov/rates", "text": "Treasury 10Y benchmark yield was 4.25 percent."}],
        enable_domain_verifiers=True,
        category="financial",
        registry=reg,
    )
    assert vr.followup_queries == []


def test_R7_pii_and_secret_tokens_in_claims_are_skipped_before_query_emission():
    reg = VerifierRegistry()
    reg.register(
        _StubDomainPlugin(
            "stub_fin",
            "financial",
            [
                _ca("Routing 021000021 account 987654321012 wire transfer disputed", "financial", "CONTESTED"),
                _ca("Public GAAP revenue growth rate disputed across filings", "financial", "INSUFFICIENT_EVIDENCE"),
            ],
        )
    )
    vr = verify_report_evidence(
        "Public GAAP revenue growth rate disputed across filings.",
        [{"url": "https://example.com/r", "text": "Public GAAP revenue growth rate disputed across filings."}],
        enable_domain_verifiers=True,
        category="financial",
        registry=reg,
    )
    assert len(vr.followup_queries) == 1
    assert "021000021" not in vr.followup_queries[0]
    assert "987654321012" not in vr.followup_queries[0]
    assert "Public GAAP revenue growth rate disputed across filings" in vr.followup_queries[0]


def test_R8_deduplication_and_hard_cap_at_four_total_queries():
    assessments = [
        _ca(f"Distinct contested regulatory provision number {i} in statute", "legal", "CONTESTED")
        for i in range(6)
    ]
    assessments.append(assessments[0])
    reg = VerifierRegistry()
    reg.register(_StubDomainPlugin("stub_legal", "legal", assessments))

    vr = verify_report_evidence(
        "AlphaGPU 999W TDP unverified spec plus legal claims.",
        [{"url": "https://example.com/l", "text": "Legal discussion."}],
        enable_domain_verifiers=True,
        category="legal",
        registry=reg,
    )
    assert len(vr.followup_queries) == 4
    assert len({q.lower() for q in vr.followup_queries}) == 4


@pytest.mark.asyncio
async def test_R9_deep_researcher_mid_loop_triggers_followup_on_domain_gaps():
    researcher = DeepResearcher(
        llm_endpoint="http://localhost:11434",
        llm_model="qwen2.5:14b",
        max_rounds=2,
        min_rounds=1,
        enable_domain_verifiers=True,
        category="financial",
    )
    executed_queries = []

    async def _fake_exec(actions, question):
        for a in actions:
            q = a.args.get("query", "")
            if q:
                executed_queries.append(q)
        return [
            {
                "url": f"https://sec.gov/filing-{len(executed_queries)}",
                "title": "SEC Filing",
                "text": "Acme Corp Q4 margin was 24 percent per 10-K.",
                "content": "Acme Corp Q4 margin was 24 percent per 10-K.",
            }
        ]

    call_count = 0

    def _fake_verify(report, findings, analyzed_urls=None, enable_domain_verifiers=False, category="general", registry=None):
        nonlocal call_count
        call_count += 1
        from src.research_verifier import VerificationReport

        if call_count == 1:
            return VerificationReport(
                followup_queries=['"Acme Corp Q4 margin" SEC filing 10-K 10-Q official financial report'],
                claim_assessments=[{"claim_assessment": "CONTESTED", "domain": "financial"}],
            )
        return VerificationReport(
            verified_facts=["Acme Corp Q4 margin: 24%"],
            claim_assessments=[{"claim_assessment": "SUPPORTED", "domain": "financial"}],
        )

    with (
        patch.object(researcher, "_maybe_warmup_large_local_model", AsyncMock()),
        patch.object(researcher, "_create_plan", AsyncMock(return_value="Plan")),
        patch.object(researcher, "_plan_research_actions", AsyncMock(return_value=[])),
        patch.object(researcher, "_generate_queries", AsyncMock(return_value=["Acme Corp Q4 margin"])),
        patch.object(researcher, "_execute_research_actions", side_effect=_fake_exec),
        patch.object(researcher, "_synthesize", AsyncMock(return_value="Acme Corp Q4 margin is 24%.")),
        patch.object(researcher, "_final_report", AsyncMock(return_value="Acme Corp Q4 margin is 24%.")),
        patch.object(researcher, "_should_stop", AsyncMock(return_value=True)),
        patch("src.deep_research.verify_report_evidence", side_effect=_fake_verify),
    ):
        out = await researcher.research("What was Acme Corp Q4 margin?")

    assert call_count == 2
    assert any("SEC filing 10-K 10-Q" in q for q in executed_queries)
    assert "Acme Corp Q4 margin is 24%." in out
