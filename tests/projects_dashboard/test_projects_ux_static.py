"""Static guarantees for the Projects settings page: deletion is typed, only unregisters, and the lists explain themselves."""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PLUGIN = (ROOT / 'integrations/hermes-mode/desktop-plugins/odysseus-resume/plugin.js').read_text(encoding='utf-8')
PATCH = (ROOT / 'integrations/hermes-mode/patches/webapp-composer-control-center.patch').read_text(encoding='utf-8')


def test_manage_tab_offers_a_typed_confirmation_before_deleting_a_project():
    assert "'Delete project\\u2026'" in PLUGIN
    assert 'Type the exact project name' in PLUGIN
    assert re.search(r"delText\.trim\(\) !== target\.name\) throw", PLUGIN)


def test_delete_uses_the_native_method_and_never_touches_files_or_grants():
    assert 'host.projects.delete(target.id)' in PLUGIN
    assert "rpc('projects.delete'" in PLUGIN            # fallback for a Desktop without host.projects
    for forbidden in ('fs/delete', 'rmtree', 'unlink', 'workspaces/revoke', 'workspaces/grant', 'add_root'):
        block = PLUGIN[PLUGIN.index('Delete project\\u2026') - 1500:PLUGIN.index('Delete project\\u2026') + 3500]
        assert forbidden not in block, forbidden


def test_delete_dialog_explains_conversations_folders_and_permissions():
    for sentence in ('Conversations are not deleted', 'every file on disk', 'File-tool write grants are not changed', 'use Archive instead'):
        assert sentence in PLUGIN


def test_lists_separate_registered_projects_from_everything_else_and_say_why():
    for label in ('Hermes projects', 'Not registered', 'Detected from conversations', "Archived"):
        assert f"groupLabel('{label}'" in PLUGIN
    assert 'its folder is missing on disk' in PLUGIN
    assert 'known from:' in PLUGIN


def test_every_project_mutation_refreshes_the_sidebar_lists():
    assert PLUGIN.count('refreshHermesLists()') >= 4
    assert 'host.projects.refresh' in PLUGIN


def test_desktop_patch_exports_only_unregistering_delete_and_refresh():
    assert 'deleteProject, refreshProjects, refreshProjectTree' in PATCH
    assert 'projects: {' in PATCH


def test_composer_never_grants_and_confirms_changes_to_an_existing_conversation():
    assert 'Choosing a folder never grants write access.' in PATCH
    assert 'setPending({ kind: \'cwd\', path: dir })' in PATCH       # existing conversation: confirmation first
    assert 'disabled={existing && busy}' in PATCH                      # no workspace move while a turn runs
    assert 'write_roots' not in PATCH and 'grant(' not in PATCH
