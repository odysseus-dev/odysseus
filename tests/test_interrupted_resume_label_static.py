from pathlib import Path
import re
from tests.helpers.stylesheets import app_css


ROOT = Path(__file__).resolve().parents[1]
CHAT = (ROOT / "static/js/chat.js").read_text()
RENDERER = (ROOT / "static/js/chatRenderer.js").read_text()
STYLE = app_css()
APP = (ROOT / "static/app.js").read_text()
INDEX = (ROOT / "static/index.html").read_text()


def test_live_interruption_controls_have_visible_resume_labels():
    assert CHAT.count('continueBtn.innerHTML = \'<span class="resume-btn-label">Resume</span>') == 2
    assert "_cont.innerHTML = '<span class=\"resume-btn-label\">Resume</span>" in CHAT


def test_restored_interruption_control_has_visible_resume_label():
    assert "continueBtn.className = 'continue-btn resume-btn';" in RENDERER
    assert 'continueBtn.innerHTML = \'<span class="resume-btn-label">Resume</span>' in RENDERER


def test_resume_label_uses_scoped_compact_button_styling():
    assert ".continue-btn.resume-btn {" in STYLE
    assert "display:inline-flex;" in STYLE
    resume_start = STYLE.index(".continue-btn.resume-btn {")
    resume_block = STYLE[resume_start:STYLE.index("    }", resume_start) + len("    }")]
    assert "border:none;" in resume_block
    assert "top:2px;" in resume_block
    assert ".continue-btn.resume-btn .resume-btn-icon" in STYLE


def test_resume_uses_control_message_instead_of_copying_partial_output():
    assert CHAT.count("msgInput.value = 'Continue from where you left off.';") == 2
    assert "cutoff.slice(-500)" not in CHAT
    assert "msgInput.value = 'Continue from where you left off.';" in RENDERER
    assert "cutoff.slice(-500)" not in RENDERER


def test_chat_controller_uses_one_module_identity():
    app_version = re.search(r"\./js/chat\.js\?v=([A-Za-z0-9_-]+)", APP)
    index_versions = re.findall(r"/static/js/chat\.js\?v=([A-Za-z0-9_-]+)", INDEX)
    assert app_version
    assert len(index_versions) == 2
    assert set(index_versions) == {app_version.group(1)}
