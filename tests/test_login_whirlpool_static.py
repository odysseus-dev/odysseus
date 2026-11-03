from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_sign_in_uses_shared_whirlpool_during_request():
    login = (ROOT / "static/login.html").read_text(encoding="utf-8")

    assert "function showSubmitWhirlpool()" in login
    assert "spinnerModule.createWhirlpool(16)" in login
    assert login.index("showSubmitWhirlpool();", login.index("form.addEventListener('submit'")) < login.index("let data = await doLogin()")
    assert "spinner.start()" not in login
    assert 'class="login-spinner"' not in login


def test_sign_in_restores_appropriate_label_after_failure():
    login = (ROOT / "static/login.html").read_text(encoding="utf-8")

    assert "function restoreSubmit()" in login
    assert "submitBtn.textContent = submitLabel()" in login
    assert "if (form._totpMode) return 'Verify'" in login
    assert login.count("restoreSubmit();") >= 4
