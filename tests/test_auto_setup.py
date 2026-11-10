"""Automatic setup flow: every external dependency is replaced with a fake."""
import asyncio
import os
import types

import pytest

import src.auto_setup as auto_setup
from src.auto_setup import AutoSetup, redact

GB = 1_000_000_000


class FakeRuntime:
    MIN_AGENT_CONTEXT = 8192

    def __init__(self, exe=None, offer=True, models=None, root=None, details=None, ctx=None,
                 tool_ok=True, create_error=False):
        self.exe = exe
        self.offer = offer
        self.models = models or []
        self.calls = []
        # Agent-model preparation: the default is "not an Ollama server".
        self.root = root
        self.details = details or {}
        self.ctx = ctx
        self.tool_ok = tool_ok
        self.create_error = create_error
        self.loaded = []
        self.created = []
        self.probed = []

    def ollama_root(self, base_url):
        return self.root

    def model_details(self, root, model):
        return self.details.get(model, {"tools": True, "num_ctx": None})

    def loaded_context(self, root, model):
        self.loaded.append(model)
        return self.ctx

    def agent_context_size(self, ram, vram):
        return 16384

    def context_variant_name(self, model, num_ctx):
        return f"{model}-ctx{num_ctx // 1024}k"

    def create_context_variant(self, root, model, num_ctx):
        if self.create_error:
            raise RuntimeError("create failed")
        self.created.append((model, num_ctx))
        return self.context_variant_name(model, num_ctx)

    def probe_tool_call(self, root, model):
        self.probed.append(model)
        return self.tool_ok

    def find_ollama(self):
        return self.exe

    def ollama_install_offer(self):
        if not self.offer:
            return None
        return {"name": "Ollama", "source_url": "https://ollama.com/download", "size_bytes": GB, "license": "MIT", "cost": "Free"}

    def install_ollama(self, progress):
        self.calls.append("install")
        progress("Installing")
        self.exe = "ollama"
        return "ollama"

    def ensure_ollama_running(self, exe):
        self.calls.append("run")
        return "http://127.0.0.1:11434"

    def list_ollama_models(self, base):
        return list(self.models)

    def recommend_model(self, ram, vram):
        return {"model": "small:1b", "size_bytes": 2 * GB, "license": "Apache-2.0"}

    def pull_model(self, base, model, progress):
        self.calls.append("pull")
        progress(GB, 2 * GB)
        progress(2 * GB, 2 * GB)
        self.models = [model]


@pytest.fixture
def env(tmp_path, monkeypatch):
    stored = {"rows": [], "created": [], "targets": [], "marked": [],
              "default": {"endpoint_id": "", "model": ""}, "switched": []}
    monkeypatch.setattr(auto_setup, "DATA_DIR", str(tmp_path))
    monkeypatch.setattr("services.hwfit.hardware.detect_system", lambda *a, **k: {"total_ram_gb": 16, "gpu_name": None, "gpu_vram_gb": None})
    monkeypatch.setattr(auto_setup, "_enabled_llm_endpoints", lambda: list(stored["rows"]))
    monkeypatch.setattr(auto_setup, "_probe_models", lambda base, key: [])
    monkeypatch.setattr(auto_setup, "_first_chat", lambda models: models[0] if models else None)
    monkeypatch.setattr(auto_setup, "_scan_this_computer", lambda d: [])
    monkeypatch.setattr(auto_setup, "_probe_chat", lambda b, k, m: {"status": "ok", "reply": "OK"})
    monkeypatch.setattr(auto_setup.shutil, "disk_usage", lambda p: types.SimpleNamespace(free=100 * GB))

    def store(target, invalidate):
        row = {"id": "ep1", "name": target["name"], "base_url": target["base_url"], "api_key": None}
        stored["rows"] = [row]
        stored["created"].append(target["base_url"])
        stored["targets"].append(target)
        return "ep1"

    monkeypatch.setattr(auto_setup, "_store_endpoint", store)
    monkeypatch.setattr(auto_setup, "_mark_tools_supported", lambda eid, inv: stored["marked"].append(eid))
    monkeypatch.setattr(auto_setup, "_current_default", lambda: dict(stored["default"]))
    monkeypatch.setattr(auto_setup, "_set_default_model", stored["switched"].append)
    monkeypatch.setattr(auto_setup, "_runtime", lambda: FakeRuntime())
    stored["use_runtime"] = lambda rt: monkeypatch.setattr(auto_setup, "_runtime", lambda: rt)
    return stored


async def settle(setup, until=("done", "failed", "needs_consent")):
    for _ in range(200):
        st = await setup.status()
        if st["state"] in until:
            return st
        await asyncio.sleep(0.01)
    raise AssertionError("setup did not settle")


def run(coro):
    return asyncio.run(coro)


def test_reuses_existing_working_endpoint(env, monkeypatch):
    env["rows"] = [{"id": "a", "name": "Mine", "base_url": "http://x/v1", "api_key": None}]
    monkeypatch.setattr(auto_setup, "_probe_models", lambda b, k: ["m1"])

    async def go():
        s = AutoSetup()
        await s.start()
        return await settle(s)

    st = run(go())
    assert st["state"] == "done" and st["result"] == {"endpoint_name": "Mine", "model": "m1"}
    assert st["needed"] is False
    assert [x["id"] for x in st["steps"]] == list(auto_setup.STEP_IDS)


def test_discovered_local_server_is_reused(env, monkeypatch):
    monkeypatch.setattr(auto_setup, "_scan_this_computer", lambda d: [{"host": "127.0.0.1", "port": 1234, "models": ["lm"]}])

    async def go():
        s = AutoSetup()
        await s.start()
        return await settle(s)

    st = run(go())
    assert st["state"] == "done" and st["result"]["model"] == "lm"
    assert env["created"] == ["http://127.0.0.1:1234/v1"]


def test_ollama_without_models_consent_pull_connect_test(env):
    rt = FakeRuntime(exe="ollama")
    env["use_runtime"](rt)

    async def go():
        s = AutoSetup()
        await s.start()
        st = await settle(s)
        assert st["state"] == "needs_consent" and st["consent"]["kind"] == "download_model"
        assert st["needed"] is True
        await s.respond(True)
        return await settle(s, until=("done", "failed"))

    st = run(go())
    assert st["state"] == "done" and st["result"]["model"] == "small:1b"
    assert st["progress"] == {"done_bytes": 2 * GB, "total_bytes": 2 * GB}
    assert rt.calls == ["run", "pull"] and env["created"] == ["http://127.0.0.1:11434/v1"]


def test_no_ollama_approve_installs_everything(env):
    rt = FakeRuntime()
    env["use_runtime"](rt)

    async def go():
        s = AutoSetup()
        await s.start()
        st = await settle(s)
        assert st["consent"]["kind"] == "install_runtime"
        assert st["consent"]["size_bytes"] == 3 * GB and "small:1b" in st["consent"]["name"]
        await s.respond(True)
        return await settle(s, until=("done", "failed"))

    st = run(go())
    assert st["state"] == "done" and st["needed"] is False
    assert rt.calls == ["install", "run", "pull"]


def test_install_consent_covers_the_longer_copy(env):
    rt = FakeRuntime(root=ROOT, ctx=4096)
    env["use_runtime"](rt)

    async def go():
        s = AutoSetup()
        await s.start()
        st = await settle(s)
        assert st["consent"]["kind"] == "install_runtime"
        await s.respond(True)
        st = await settle(s)  # a second question would stop here
        await s.start()  # a later run downloads nothing, so the same copy needs its own question
        assert (await settle(s))["consent"]["kind"] == "add_model_copy"
        return st

    st = run(go())
    assert st["state"] == "done" and st["result"]["model"] == "small:1b-ctx16k"
    assert rt.created == [("small:1b", 16384)]


def test_download_consent_covers_the_longer_copy(env):
    rt = FakeRuntime(exe="ollama", root="http://127.0.0.1:11434", ctx=4096)
    st = run_ollama(env, rt, answers=[True])
    assert [c["kind"] for c in env["consents"]] == ["download_model"]
    assert st["state"] == "done" and rt.created == [("small:1b", 16384)]


def test_no_ollama_decline_installs_nothing(env):
    rt = FakeRuntime()
    env["use_runtime"](rt)

    async def go():
        s = AutoSetup()
        await s.start()
        await settle(s)
        await s.respond(False)
        return await settle(s, until=("failed",))

    st = run(go())
    assert st["state"] == "failed" and "Nothing was installed" in st["error"]["message"]
    assert rt.calls == [] and env["created"] == []


def test_unsupported_os_offer_none(env):
    env["use_runtime"](FakeRuntime(offer=False))

    async def go():
        s = AutoSetup()
        await s.start()
        return await settle(s)

    st = run(go())
    assert st["state"] == "failed" and "Settings > Models" in st["error"]["hint"]


def test_not_enough_disk_space(env, monkeypatch):
    env["use_runtime"](FakeRuntime())
    monkeypatch.setattr(auto_setup.shutil, "disk_usage", lambda p: types.SimpleNamespace(free=GB))

    async def go():
        s = AutoSetup()
        await s.start()
        await settle(s)
        await s.respond(True)
        return await settle(s, until=("failed",))

    assert "disk space" in run(go())["error"]["message"]


def test_probe_failure_marks_failed_with_repair_hint(env, monkeypatch):
    monkeypatch.setattr(auto_setup, "_scan_this_computer", lambda d: [{"host": "127.0.0.1", "port": 1234, "models": ["lm"]}])
    monkeypatch.setattr(auto_setup, "_probe_chat", lambda b, k, m: {"status": "ok", "reply": ""})

    async def go():
        s = AutoSetup()
        await s.start()
        return await settle(s)

    st = run(go())
    assert st["state"] == "failed" and "Repair" in st["error"]["hint"]
    assert st["steps"][-1]["status"] == "failed"


def test_unsupported_computer(env, monkeypatch):
    monkeypatch.setattr(auto_setup.platform, "machine", lambda: "riscv64")

    async def go():
        s = AutoSetup()
        await s.start()
        return await settle(s)

    assert run(go())["state"] == "failed"


def test_start_is_idempotent_while_waiting_for_consent(env):
    env["use_runtime"](FakeRuntime())

    async def go():
        s = AutoSetup()
        await s.start()
        await settle(s)
        task = s._task
        st = await s.start()
        assert s._task is task and st["state"] == "needs_consent"
        await s.respond(False)
        await settle(s, until=("failed",))
        await s.start()
        assert s._task is not task  # a finished run starts fresh
        await settle(s)

    run(go())


def test_report_redacts_home_and_secrets(env):
    home = os.path.expanduser("~")
    s = AutoSetup()
    s._log(f"opened {home}\\models with api_key=abc123 and Bearer sk-abcdefgh12345678")
    rep = s.report()
    assert home not in rep and "abc123" not in rep and "sk-abcdefgh" not in rep
    assert "Odysseus" in rep and "OS:" in rep


def test_redact_plain_text_untouched():
    assert redact("Downloading small:1b") == "Downloading small:1b"


def test_installer_error_message_reaches_the_user(env):
    class RuntimeInstallError(Exception):
        pass

    rt = FakeRuntime()
    def offline():
        raise RuntimeInstallError("Could not reach ollama.com to get the installer.")
    rt.ollama_install_offer = offline
    rt.find_ollama = lambda: None
    env["use_runtime"](rt)

    async def go():
        setup = auto_setup.AutoSetup()
        await setup.start()
        for _ in range(200):
            st = await setup.status()
            if st["state"] == "failed":
                return st
            await asyncio.sleep(0.01)
        return st

    st = asyncio.run(go())
    assert st["state"] == "failed"
    assert st["error"]["message"] == "Could not reach ollama.com to get the installer."


# ---- agent model preparation ---------------------------------------------------

ROOT = "http://127.0.0.1:11434"


def run_ollama(env, rt, answers=()):
    """Run setup; each entry in answers replies to one consent question, which is kept in env["consents"]."""
    env["use_runtime"](rt)
    env["consents"] = []

    async def go():
        s = AutoSetup()
        await s.start()
        st = await settle(s)
        for approve in answers:
            assert st["state"] == "needs_consent"
            env["consents"].append(st["consent"])
            await s.respond(approve)
            st = await settle(s)
        return st

    return run(go())


def test_non_ollama_server_changes_nothing(env, monkeypatch):
    rt = FakeRuntime()
    env["use_runtime"](rt)
    monkeypatch.setattr(auto_setup, "_scan_this_computer", lambda d: [{"host": "127.0.0.1", "port": 1234, "models": ["lm"]}])

    async def go():
        s = AutoSetup()
        await s.start()
        return await settle(s)

    st = run(go())
    assert st["state"] == "done" and list(st["result"]) == ["endpoint_name", "model"]
    assert rt.loaded == [] and rt.created == [] and rt.probed == [] and env["marked"] == []


def test_explicit_long_context_is_chosen_without_loading(env):
    rt = FakeRuntime(exe="ollama", models=["a", "b"], root=ROOT,
                     details={"a": {"tools": False, "num_ctx": None}, "b": {"tools": True, "num_ctx": 16384}})
    st = run_ollama(env, rt)
    assert st["result"]["model"] == "b"
    assert env["targets"][0]["models"] == ["b", "a"]
    assert rt.loaded == [] and rt.created == []


def test_measured_context_large_enough_keeps_model(env):
    rt = FakeRuntime(exe="ollama", models=["a"], root=ROOT, ctx=8192)
    st = run_ollama(env, rt)
    assert st["result"]["model"] == "a" and rt.loaded == ["a"] and rt.created == []


def test_short_context_asks_then_creates_a_copy_and_uses_it(env):
    rt = FakeRuntime(exe="ollama", models=["a", "b"], root=ROOT, ctx=4096)
    st = run_ollama(env, rt, answers=[True])
    assert env["consents"] == [{"kind": "add_model_copy", "name": "a-ctx16k", "size_bytes": 0, "cost": "Free",
                                "license": "Same as a", "source_url": "Made on this computer from a"}]
    assert rt.created == [("a", 16384)]
    assert st["result"]["model"] == "a-ctx16k"
    assert env["targets"][0]["model"] == "a-ctx16k"
    assert env["targets"][0]["models"] == ["a-ctx16k", "a", "b"]
    assert "note" not in st["result"]


def test_nothing_is_added_while_the_copy_question_is_open(env):
    rt = FakeRuntime(exe="ollama", models=["a"], root=ROOT, ctx=4096)
    st = run_ollama(env, rt)
    assert st["state"] == "needs_consent" and st["consent"]["kind"] == "add_model_copy"
    assert rt.created == [] and env["created"] == []


def test_declined_copy_keeps_model_and_adds_note(env):
    rt = FakeRuntime(exe="ollama", models=["a"], root=ROOT, ctx=4096)
    st = run_ollama(env, rt, answers=[False])
    assert st["state"] == "done" and st["result"]["model"] == "a"
    assert rt.created == [] and env["marked"] == [] and env["switched"] == []
    assert st["result"]["note"] == "Chat is ready. " + auto_setup.NOTE_COPY_DECLINED


def test_answer_for_another_question_is_ignored_and_repair_asks_again(env):
    rt = FakeRuntime(exe="ollama", models=["a"], root=ROOT, ctx=4096)
    env["use_runtime"](rt)

    async def go():
        s = AutoSetup()
        await s.start()
        await settle(s)
        st = await s.respond(True, "install_runtime", "Ollama")  # an old tab that shows another question
        assert st["state"] == "needs_consent" and st["consent"]["kind"] == "add_model_copy" and rt.created == []
        await s.respond(False, "add_model_copy", "a-ctx16k")
        assert (await settle(s))["state"] == "done" and rt.created == []
        await s.start()
        await settle(s)
        await s.respond(True, "add_model_copy", "a-ctx16k")
        return await settle(s)

    st = run(go())
    assert st["state"] == "done" and rt.created == [("a", 16384)]


def test_tool_capable_model_is_preferred(env):
    rt = FakeRuntime(exe="ollama", models=["plain", "tooly"], root=ROOT, ctx=8192,
                     details={"plain": {"tools": False, "num_ctx": None}})
    st = run_ollama(env, rt)
    assert st["result"]["model"] == "tooly" and rt.loaded == ["tooly"]
    assert env["targets"][0]["models"] == ["tooly", "plain"]


def test_failed_copy_keeps_model_and_adds_note(env):
    rt = FakeRuntime(exe="ollama", models=["a"], root=ROOT, ctx=4096, create_error=True)
    st = run_ollama(env, rt, answers=[True])
    assert st["state"] == "done" and st["result"]["model"] == "a"
    assert auto_setup.NOTE_SHORT_MEMORY in st["result"]["note"]
    assert env["marked"] == []  # native tool calls would not fit in the short context


def test_passed_tool_probe_marks_endpoint(env):
    rt = FakeRuntime(exe="ollama", models=["a"], root=ROOT, ctx=8192)
    st = run_ollama(env, rt)
    assert env["marked"] == ["ep1"] and rt.probed == ["a"]
    assert "note" not in st["result"]


def test_failed_tool_probe_adds_note_and_marks_nothing(env):
    rt = FakeRuntime(exe="ollama", models=["a"], root=ROOT, ctx=8192, tool_ok=False)
    st = run_ollama(env, rt)
    assert st["state"] == "done" and env["marked"] == []
    assert st["result"]["note"] == "Chat is ready. " + auto_setup.NOTE_NO_TOOLS


def test_model_without_tool_support_is_not_copied(env):
    rt = FakeRuntime(exe="ollama", models=["a"], root=ROOT, ctx=4096, tool_ok=False,
                     details={"a": {"tools": False, "num_ctx": None}})
    st = run_ollama(env, rt)
    assert rt.loaded == [] and rt.created == []
    assert st["result"]["note"] == "Chat is ready. " + auto_setup.NOTE_NO_TOOLS


# ---- repair with a default model already set ----------------------------------

OLLAMA_ROW = {"id": "ep1", "name": "This computer (Ollama)", "base_url": ROOT + "/v1", "api_key": None}


def test_repair_gives_the_default_model_a_longer_copy(env):
    env["rows"] = [dict(OLLAMA_ROW)]
    env["default"] = {"endpoint_id": "ep1", "model": "a"}
    rt = FakeRuntime(exe="ollama", models=["b", "a"], root=ROOT, ctx=4096)
    st = run_ollama(env, rt, answers=[True])
    assert env["consents"][0]["kind"] == "add_model_copy"
    assert rt.loaded == ["a"] and rt.created == [("a", 16384)]
    assert st["result"]["model"] == "a-ctx16k" and env["switched"] == ["a-ctx16k"]
    assert env["marked"] == ["ep1"]


def test_repair_reuses_an_existing_longer_copy(env):
    env["rows"] = [dict(OLLAMA_ROW)]
    env["default"] = {"endpoint_id": "ep1", "model": "a"}
    rt = FakeRuntime(exe="ollama", models=["a", "a-ctx16k"], root=ROOT, ctx=4096,
                     details={"a-ctx16k": {"tools": True, "num_ctx": 16384}})
    st = run_ollama(env, rt)
    assert rt.created == [] and env["switched"] == ["a-ctx16k"]
    assert st["result"]["model"] == "a-ctx16k"


def test_repair_keeps_a_default_model_with_enough_memory(env):
    env["rows"] = [dict(OLLAMA_ROW)]
    env["default"] = {"endpoint_id": "ep1", "model": "a"}
    rt = FakeRuntime(exe="ollama", models=["b", "a"], root=ROOT, ctx=8192)
    st = run_ollama(env, rt)
    assert st["result"]["model"] == "a" and rt.loaded == ["a"]
    assert rt.created == [] and env["switched"] == [] and env["marked"] == ["ep1"]


def test_default_on_another_endpoint_is_left_alone(env):
    env["rows"] = [{"id": "cloud", "name": "Cloud", "base_url": "https://api.example.com/v1", "api_key": None}]
    env["default"] = {"endpoint_id": "cloud", "model": "big"}
    rt = FakeRuntime(exe="ollama", models=["a"], root=ROOT, ctx=4096)
    st = run_ollama(env, rt)
    assert st["state"] == "done" and list(st["result"]) == ["endpoint_name", "model"]
    assert rt.loaded == [] and rt.created == [] and rt.probed == []
    assert env["marked"] == [] and env["switched"] == []


def test_endpoint_and_model_in_use_are_tested_first(env, monkeypatch):
    env["rows"] = [{"id": "x", "name": "First", "base_url": "http://x/v1", "api_key": None},
                   {"id": "y", "name": "Mine", "base_url": "http://y/v1", "api_key": None}]
    env["default"] = {"endpoint_id": "y", "model": "m2"}
    monkeypatch.setattr(auto_setup, "_probe_models", lambda base, key: ["m1", "m2"])
    st = run_ollama(env, FakeRuntime())
    assert st["result"] == {"endpoint_name": "Mine", "model": "m2"}


def test_mark_tools_supported_sets_only_unset_values(tmp_path, monkeypatch):
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    import core.database as database

    engine = create_engine(f"sqlite:///{tmp_path / 'm.db'}")
    database.Base.metadata.create_all(bind=engine)
    Session = sessionmaker(bind=engine)
    monkeypatch.setattr(database, "SessionLocal", Session)
    db = Session()
    for eid, value in (("auto", None), ("off", False)):
        db.add(database.ModelEndpoint(id=eid, name=eid, base_url=f"http://x/{eid}", supports_tools=value))
    db.commit()
    db.close()
    seen = []
    for eid in ("auto", "off"):
        auto_setup._mark_tools_supported(eid, lambda eid=eid: seen.append(eid))
    db = Session()
    got = {r.id: r.supports_tools for r in db.query(database.ModelEndpoint).all()}
    db.close()
    assert got == {"auto": True, "off": False} and seen == ["auto", "off"]
