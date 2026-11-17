import re
import subprocess
from pathlib import Path

from tests.helpers.stylesheets import app_css


ROOT = Path(__file__).resolve().parent.parent
SKILLS_JS = (ROOT / "static/js/skills.js").read_text(encoding="utf-8")
METRICS_JS = (ROOT / "static/js/skillsMetrics.js").read_text(encoding="utf-8")
STYLE = app_css()
INDEX = (ROOT / "static/index.html").read_text(encoding="utf-8")


def test_skills_cards_show_saved_turn_estimates_from_audit():
    assert "function _turnSavingsHtml" in SKILLS_JS
    assert "auditNumber(sk, 'saved_turns')" in SKILLS_JS
    assert "auditNumber(sk, 'saved_tool_calls')" in SKILLS_JS
    assert "saves ${turns}t" in SKILLS_JS
    assert "_skillStatsHtml(sk)" in SKILLS_JS
    assert "skill-turns-saved" in STYLE


def test_skills_can_sort_by_saved_turns():
    assert '<option value="sort:saved-turns">Most saved turns</option>' in INDEX
    assert "_skillsSort === 'saved-turns'" in SKILLS_JS
    assert "const bt = auditNumber(b, 'saved_turns')" in SKILLS_JS
    assert "const at = auditNumber(a, 'saved_turns')" in SKILLS_JS


def test_skills_summary_surfaces_audit_health_and_quick_filters():
    assert '<div id="skills-summary" class="skills-summary-strip"' in INDEX
    assert "await _loadSkillApprovalThreshold();" in SKILLS_JS
    assert "skillsSummaryMetrics(userSkills, _skillApprovalThreshold)" in SKILLS_JS
    assert "_summaryChip('all', 'All'" in SKILLS_JS
    assert "_summaryChip('approved', 'Approved'" in SKILLS_JS
    assert "_summaryChip('builtin', 'Built-in'" in SKILLS_JS
    assert "_summaryChip('draft', 'Draft'" in SKILLS_JS
    summary = SKILLS_JS.split('function _renderSkillsSummary()', 1)[1].split('function _sortSkills', 1)[0]
    assert summary.count('_summaryChip(') == 4
    assert ".skills-summary-chip.active" in STYLE


def test_skills_summary_metrics_and_quick_filters_behave():
    assert "export function skillsSummaryMetrics" in METRICS_JS
    assert "export function filterSkillsByQuickFilter" in METRICS_JS
    script = """
        import assert from 'node:assert/strict';
        import {
          auditNumber,
          filterSkillsByQuickFilter,
          skillsSummaryMetrics,
        } from './static/js/skillsMetrics.js';

        const skills = [
          { name: 'fast', status: 'published', confidence: 0.95, audit_verdict: 'pass', saved_turns: 3 },
          { name: 'slow', status: 'published', confidence: 0.95, audit_verdict: 'pass', saved_turns: -1, saved_tool_calls: -1, baseline_verdict: 'worse', usefulness: 0.1 },
          { name: 'drafty', status: 'draft', confidence: 0.6 },
          { name: 'dup', status: 'draft', confidence: 0.9, audit_verdict: 'pass', necessity: { necessary: false, redundant_with: ['fast'], reason: 'duplicate' } },
          { name: 'broad-zero', status: 'draft', confidence: 0.9, audit_verdict: 'pass', saved_turns: 0, necessity: { necessary: false, redundant_with: [], reason: 'generic but maybe useful' } },
        ];

        assert.equal(auditNumber({ verdict: { saved_turns: '4' } }, 'saved_turns'), 4);
        assert.deepEqual(skillsSummaryMetrics(skills, 0.85), {
            total: 5,
            approved: 2,
            proven: 1,
            needsAudit: 1,
            audited: 4,
            review: 2,
            drafts: 3,
            binned: 0,
          savedTurns: 3,
        });
        assert.deepEqual(filterSkillsByQuickFilter(skills, 'saved').map(s => s.name), ['fast']);
        assert.deepEqual(filterSkillsByQuickFilter(skills, 'approved').map(s => s.name), ['fast', 'slow']);
        assert.deepEqual(filterSkillsByQuickFilter(skills, 'unaudited').map(s => s.name), ['drafty']);
        assert.deepEqual(filterSkillsByQuickFilter(skills, 'review').map(s => s.name), ['dup', 'broad-zero']);
        assert.deepEqual(filterSkillsByQuickFilter(skills, 'draft').map(s => s.name), ['drafty', 'dup', 'broad-zero']);
    """
    result = subprocess.run(
        ["node", "--input-type=module", "-e", script],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr


def test_broad_scope_skill_pill_is_neutral_not_strong_red():
    assert "kind === 'trivial' ? 'broad'" in SKILLS_JS

    block = re.search(
        r"\.skill-necessity-trivial\s*\{(?P<body>.*?)\n\}",
        STYLE,
        flags=re.S,
    )
    assert block is not None
    assert "var(--color-danger" not in block.group("body")


def test_skills_bundle_is_cache_busted_for_saved_turn_ui():
    service_worker = (ROOT / "static/sw.js").read_text(encoding="utf-8")
    index_version = re.search(r"/static/js/skills\.js\?v=([A-Za-z0-9_-]+)", INDEX)
    sw_version = re.search(r"/static/js/skills\.js\?v=([A-Za-z0-9_-]+)", service_worker)
    assert index_version and sw_version
    assert index_version.group(1) == sw_version.group(1)


def test_skill_card_statuses_are_compact_and_unambiguous():
    assert 'class="skill-card-title-row"' in SKILLS_JS
    assert "sk.source === 'builtin' ? _sourcePill(sk) : ''" in SKILLS_JS
    assert 'class="memory-cat-badge skill-status-pill skill-status-published"' in SKILLS_JS
    assert "skillIsApproved(sk, _skillApprovalThreshold) ? _statusPill(sk) : ''" in SKILLS_JS
    assert 'Queued for automatic skill check' in SKILLS_JS
    assert 'Eligible drafts are reviewed automatically.' in SKILLS_JS
    assert 'esc(model + reason)' in SKILLS_JS
    assert ".skill-status-published" in STYLE
    assert ".skill-card:not(.doclib-card-expanded) .skill-card-tags { display: none; }" in STYLE


def test_skills_summary_follows_the_search_toolbar():
    toolbar_end = INDEX.index('</div>\n            <div id="skills-summary"')
    search = INDEX.index('id="skills-search"')
    skills_list = INDEX.index('id="skills-list"')
    assert search < toolbar_end < skills_list


def test_skills_summary_chips_scroll_horizontally_without_wrapping():
    assert "flex-wrap: nowrap;" in STYLE
    assert "overflow-x: auto;" in STYLE
    assert ".skills-summary-strip::-webkit-scrollbar { display: none; }" in STYLE


def test_total_skill_count_and_all_filter_include_their_respective_scopes():
    assert "elH.textContent = String(userSkills.length || 0)" in SKILLS_JS
    assert "_summaryChip('all', 'All', skills.length" in SKILLS_JS


def test_builtin_skills_are_preapproved_and_excluded_from_audit_queues():
    assert "if (sk && sk.source === 'builtin') return false" in METRICS_JS
    assert "if (sk.source === 'builtin') return sk.status === 'published'" in METRICS_JS
    assert ".filter(sk => sk.source !== 'builtin' && sk.status !== 'binned')" in SKILLS_JS

    script = """
        import assert from 'node:assert/strict';
        import {
          filterSkillsByQuickFilter,
          skillsSummaryMetrics,
        } from './static/js/skillsMetrics.js';

        const skills = [
          { name: 'core', source: 'builtin', status: 'published', confidence: 1 },
          { name: 'draft', source: 'user', status: 'draft', confidence: 0.5 },
        ];
        assert.deepEqual(skillsSummaryMetrics(skills, 0.85), {
          total: 2,
          approved: 1,
          proven: 1,
          needsAudit: 1,
              audited: 0,
              review: 0,
              drafts: 1,
              binned: 0,
              savedTurns: 0,
        });
        assert.deepEqual(filterSkillsByQuickFilter(skills, 'approved').map(s => s.name), []);
        assert.deepEqual(filterSkillsByQuickFilter(skills, 'builtin').map(s => s.name), ['core']);
        assert.deepEqual(filterSkillsByQuickFilter(skills, 'unaudited').map(s => s.name), ['draft']);
    """
    result = subprocess.run(
        ["node", "--input-type=module", "-e", script],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
