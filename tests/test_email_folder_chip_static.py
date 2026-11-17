from pathlib import Path
from tests.helpers.stylesheets import app_css
from tests.helpers.js_modules import email_library_source


ROOT = Path(__file__).resolve().parents[1]


def test_folder_chip_stays_with_date_and_moves_down():
    source = email_library_source()
    css = app_css()

    assert 'class="email-meta-date-group"' in source
    group_markup = source[source.index('class="email-meta-date-group"'):][:180]
    assert 'class="email-meta-date"' in group_markup
    assert "${folderChip}" in group_markup
    first_folder_rule = css.index(".email-folder-chip {")
    folder_rule_start = css.index(".email-folder-chip {", first_folder_rule + 1)
    folder_rule = css[folder_rule_start:][:220]
    assert "position: relative;" in folder_rule
    assert "top: 4px;" in folder_rule
    group_rule = css[css.index(".email-meta-date-group {"):][:180]
    assert "display: inline-flex;" in group_rule
    assert "white-space: nowrap;" in group_rule
