"""The Brain > Add Memory form must be submittable (#5828).

The form previously had no submit button and relied on a deprecated
``keypress`` listener for Enter, which is not guaranteed to fire on all
platforms — leaving the form with no working submit path. Pins:

- a visible, keyboard-accessible submit button next to the category select;
- the button wired to ``memoryModule.addNewMemory()``;
- Enter handled via ``keydown`` with ``preventDefault()`` (and no lingering
  ``keypress`` handler on the input).
"""
from pathlib import Path

APP_JS = Path("static/app.js")
INDEX_HTML = Path("static/index.html")


def _add_memory_row(html):
    start = html.index('id="new-memory-input"')
    end = html.index("</div>", html.index('id="new-memory-add-btn"', start))
    return html[start:end]


def test_add_memory_form_renders_a_submit_button():
    html = INDEX_HTML.read_text()
    row = _add_memory_row(html)

    assert 'id="new-memory-category"' in row, "button must sit in the same row as the form fields"
    btn_start = row.index('id="new-memory-add-btn"')
    btn_tag = row[row.rindex("<button", 0, btn_start):row.index(">", btn_start)]
    assert 'type="button"' in btn_tag, "must not rely on implicit submit semantics"


def _new_memory_wiring_block(source):
    start = source.index("const newMemoryInput = el('new-memory-input');")
    end = source.index("// Voice recording", start)
    return source[start:end]


def test_submit_button_is_wired_to_add_new_memory():
    block = _new_memory_wiring_block(APP_JS.read_text())

    assert "el('new-memory-add-btn')" in block
    assert "addEventListener('click', () => memoryModule.addNewMemory())" in block


def test_enter_uses_keydown_with_prevent_default():
    block = _new_memory_wiring_block(APP_JS.read_text())

    assert "addEventListener('keydown'" in block
    assert "addEventListener('keypress'" not in block, "keypress is deprecated and unreliable for Enter"
    assert "e.preventDefault();" in block
    assert "!e.isComposing" in block, "IME composition must not submit the form"
    assert "memoryModule.addNewMemory();" in block
