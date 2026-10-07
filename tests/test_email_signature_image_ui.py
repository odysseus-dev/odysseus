"""The settings form's signature-image controls.

The settings module pulls in the DOM and a dozen siblings, so these read
the source rather than booting it — the same idiom as the other email UI
tests.

What these pin is the part that is easy to get wrong by accident: the
three-state upload field. A pending upload, an explicit removal and "the
user never touched it" are different things, and collapsing the last two
would blank every account's logo on the next unrelated save.
"""

from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="module")
def settings():
    return (_REPO / "static" / "js" / "settings.js").read_text(encoding="utf-8")


# ── The controls ──────────────────────────────────────────────────

def test_the_form_has_a_picker_a_preview_and_a_remove_button(settings):
    assert "uf-email-signature-file" in settings
    assert "uf-email-signature-img" in settings
    assert "uf-email-signature-clear" in settings


def test_the_picker_only_offers_the_formats_the_server_accepts(settings):
    assert 'accept="image/png,image/jpeg,image/gif"' in settings


def test_the_file_input_is_hidden_behind_a_styled_button(settings):
    """A raw file input does not match anything else in this form."""
    field = settings[settings.index('id="uf-email-signature-file"'):]
    field = field[: field.index(">")]
    assert 'style="display:none;"' in field
    assert "uf-email-signature-pick" in settings
    assert "el('uf-email-signature-file').click()" in settings


def test_the_controls_reuse_the_existing_button_class(settings):
    block = settings[settings.index("uf-email-signature-pick") - 400:]
    block = block[: block.index("uf-email-signature-img-msg")]
    assert "admin-btn-add" in block
    assert "class=\"btn" not in block


def test_the_preview_uses_theme_variables_rather_than_fixed_colours(settings):
    start = settings.index('id="uf-email-signature-img"')
    row = settings[start: start + 300]
    assert "var(--border)" in row
    assert "var(--card)" in row


# ── The three states ──────────────────────────────────────────────

def test_an_untouched_field_is_left_out_of_the_save_body(settings):
    """Absent means "leave the stored image alone". If the form always sent
    a value, saving an unrelated field would blank the logo."""
    assert "let _sigImage = null;" in settings
    assert "if (_sigImage !== null) body.signature_image = _sigImage;" in settings


def test_removing_sends_an_empty_string_rather_than_omitting_the_key(settings):
    clear = settings[settings.index("_sigClearBtn.addEventListener"):]
    clear = clear[: clear.index("});")]
    assert "_sigImage = ''" in clear


def test_choosing_a_file_stores_it_as_a_data_url(settings):
    assert "readAsDataURL" in settings


# ── Guardrails ────────────────────────────────────────────────────

def test_the_size_is_checked_before_uploading(settings):
    """A 5 MB photo should fail immediately, not after the round trip."""
    assert "const _SIG_IMG_MAX = 256 * 1024;" in settings
    assert "file.size > _SIG_IMG_MAX" in settings


def test_the_client_cap_matches_the_server_cap(settings):
    from src.email_signature import MAX_SIGNATURE_IMAGE_BYTES

    assert MAX_SIGNATURE_IMAGE_BYTES == 256 * 1024
    assert "256 * 1024" in settings


def test_an_oversized_file_is_not_kept_as_a_pending_upload(settings):
    """Otherwise the next save would ship the file the user was told was
    too large."""
    handler = settings[settings.index("el('uf-email-signature-file').addEventListener"):]
    handler = handler[: handler.index("_sigClearBtn.addEventListener")]
    reject = handler[handler.index("file.size > _SIG_IMG_MAX"):]
    reject = reject[: reject.index("const reader")]
    assert "_sigImage" not in reject
    assert "return;" in reject


def test_a_pending_change_says_it_is_not_saved_yet(settings):
    """The preview updates immediately, which otherwise reads as saved."""
    assert "Saved when you save the account." in settings
    assert "Removed when you save the account." in settings


# ── Loading an existing image ─────────────────────────────────────

def test_an_existing_image_is_loaded_from_its_own_route(settings):
    assert "/signature-image" in settings
    assert "existing.has_signature_image" in settings


def test_the_preview_url_is_cache_busted(settings):
    """The URL does not change when the image behind it does, so a new
    upload would otherwise keep showing the old logo."""
    line = [ln for ln in settings.split("\n") if "signature-image?" in ln][0]
    assert "Date.now()" in line


def test_the_bytes_are_not_expected_in_the_account_list(settings):
    """The list route advertises the image with a flag; asking it for the
    data would quietly start shipping base64 to every settings load."""
    assert "existing.signature_image" not in settings.replace(
        "existing.signature_image_mime", ""
    )
