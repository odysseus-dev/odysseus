"""Update protection (sixth patch) and the Access-tab wording.

Behavioural checks run update_guard_scenarios.py inside a patched Hermes tree (HERMES_PATCHED_SRC, built with all six patches) using the
Hermes interpreter (HERMES_PATCHED_PYTHON). The scenarios call detection and preflight functions only: no updater, git or network."""
import json
import os
import subprocess
import tempfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
PATCH = (ROOT / "integrations/hermes-mode/patches/webapp-update-protection.patch").read_text(encoding="utf-8")
PLUGIN = (ROOT / "integrations/hermes-mode/desktop-plugins/odysseus-resume/plugin.js").read_text(encoding="utf-8")
SCEN = Path(__file__).with_name("update_guard_scenarios.py")
SRC, PY = os.environ.get("HERMES_PATCHED_SRC"), os.environ.get("HERMES_PATCHED_PYTHON")
MESSAGE = ("This Hermes installation is managed by Odysseus. Direct updates are disabled to protect custom integrations. "
           "Use the reviewed Odysseus update process.")


def _added():
    return "\n".join(l[1:] for l in PATCH.splitlines() if l.startswith("+") and not l.startswith("+++"))


def test_patch_is_narrow_and_touches_no_unrelated_surface():
    files = {l.split(" b/")[-1] for l in PATCH.splitlines() if l.startswith("diff --git")}
    assert files == {".odysseus-managed.json", "gateway/slash_commands.py", "hermes_cli/cli_commands_mixin.py", "hermes_cli/main.py",
                     "hermes_cli/odysseus_managed.py", "hermes_cli/update_contract.py", "hermes_cli/web_routers/actions.py", "pm/cli.py"}
    added = _added()
    assert "HERMES_MANAGED" not in added and ".managed" not in added.replace(".odysseus-managed", "")   # never the global switch that gates config writes
    assert "This Hermes installation is managed by Odysseus. Direct updates are disabled to protect custom integrations. " in added
    assert "Use the reviewed Odysseus update process." in added


def test_every_direct_update_path_calls_the_one_guard():
    for path in ("gateway/slash_commands.py", "hermes_cli/cli_commands_mixin.py", "hermes_cli/main.py", "hermes_cli/web_routers/actions.py", "pm/cli.py"):
        block = PATCH.split(f"diff --git a/{path} ")[1].split("diff --git")[0]
        assert "odysseus_managed_refusal" in block, path


def test_discovery_is_kept_by_the_guard_shape():
    main = PATCH.split("diff --git a/hermes_cli/main.py ")[1].split("diff --git")[0]
    assert 'not getattr(args, "check", False)' in main
    pm = PATCH.split("diff --git a/pm/cli.py ")[1].split("diff --git")[0]
    assert "if not args.check:" in pm
    actions = PATCH.split("diff --git a/hermes_cli/web_routers/actions.py ")[1].split("diff --git")[0]
    assert "payload[\"message\"] = managed.message" in actions and "update_available" in actions


def test_marker_ships_in_the_patch_and_says_what_it_is_not():
    marker = json.loads(PATCH.split("+++ b/.odysseus-managed.json")[1].split("diff --git")[0].replace("\n+", "\n").split("@@", 2)[2].strip().lstrip("+") or "{}")
    assert marker["managed_by"] == "odysseus" and "not a sandbox" in marker["note"]


def test_access_wording_is_accurate_and_not_overclaiming():
    for text in ("File-tool write access", "File-tool write roots on this machine", "They are not an operating-system sandbox.",
                 "Terminal commands and MCP tools are not necessarily confined to these folders.",
                 "depends on how the command is classified, not on where it writes.", "A grant does not protect against every possible write."):
        assert text in PLUGIN, text
    assert "'Write access'" not in PLUGIN and "'Write access (some folders)'" not in PLUGIN and "'No write grant'" not in PLUGIN
    assert "protects every" not in PLUGIN.lower().replace("does not protect against every", "")


def test_odysseus_projects_page_uses_the_same_label():
    js = (ROOT / "static/js/projects.js").read_text(encoding="utf-8")
    assert "'File-tool write access'" in js and "'No file-tool write grant'" in js and "'Write access'" not in js


def test_previously_shipped_resume_plugin_stays_upgradeable():
    from src.hermes_webapp_component import KNOWN_PREVIOUS_RESUME_SHA256
    joined = "".join(h[:12] for h in KNOWN_PREVIOUS_RESUME_SHA256)
    assert "0c439ab6ab31" in joined and "6909ac35e316" in joined          # the copy deployed with b1e8abf79b78, CRLF and LF forms


@pytest.fixture(scope="module")
def out():
    if not (SRC and PY):
        pytest.skip("HERMES_PATCHED_SRC / HERMES_PATCHED_PYTHON not set")
    env = {k: v for k, v in os.environ.items() if k != "PYTHONPATH"}
    r = subprocess.run([PY, str(SCEN), SRC, tempfile.mkdtemp(prefix="upd_home_")], capture_output=True, text=True, env=env, timeout=240)
    line = next((l for l in r.stdout.splitlines() if l.startswith("@@")), None)
    assert line, r.stdout[-500:] + r.stderr[-800:]
    return json.loads(line[2:])


def test_marker_states_fail_safe(out):
    s = out["states"]
    assert s["shipped_marker"] == "managed" and s["marker_missing_in_managed_layout"] == "managed"
    assert s["marker_bad_json"] == s["marker_wrong_owner"] == s["marker_empty"] == "invalid"
    assert s["independent_git_install"] == "none" and s["plain_dir"] == "none" and out["missing_path"] == "none"


def test_refusal_text_and_codes(out):
    r = out["refusals"]
    assert r["shipped_marker"]["msg"] == MESSAGE and r["shipped_marker"]["code"] == "odysseus-managed"
    assert r["marker_bad_json"]["msg"].startswith(MESSAGE) and "damaged" in r["marker_bad_json"]["msg"]
    assert r["independent_git_install"] is None and r["plain_dir"] is None


def test_apply_is_refused_before_any_updater_step(out):
    for key in ("preflight_apply_managed", "preflight_apply_invalid", "preflight_apply_layout_only"):
        assert out[key]["exit"] == 2 and MESSAGE in out[key]["text"] and out[key]["reached"] == [], key


def test_check_still_reports_and_independent_installs_are_untouched(out):
    assert out["preflight_check_managed"]["exit"] is None and out["preflight_check_managed"]["reached"] == ["check"]
    assert out["preflight_apply_independent"] == {"exit": None, "handled": False, "text": "", "reached": ["admission"]}
