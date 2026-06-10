import importlib.util
import json
import builtins
from pathlib import Path


def _load_setup_module():
    spec = importlib.util.spec_from_file_location("odysseus_setup_under_test", Path("setup.py"))
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_create_default_admin_normalizes_env_username(tmp_path, monkeypatch):
    setup_module = _load_setup_module()
    monkeypatch.setattr(setup_module, "AUTH_FILE", str(tmp_path / "auth.json"))
    monkeypatch.setenv("ODYSSEUS_ADMIN_USER", " AdminUser ")
    monkeypatch.setenv("ODYSSEUS_ADMIN_PASSWORD", "temporary-password")

    assert setup_module.create_default_admin() == "created"

    auth_path = tmp_path / "auth.json"
    data = json.loads(auth_path.read_text(encoding="utf-8"))
    assert "adminuser" in data["users"]
    assert "AdminUser" not in data["users"]


def test_configure_docker_target_nvidia_linux_uncomments_compose_file(tmp_path):
    setup_module = _load_setup_module()
    env_path = tmp_path / ".env"
    env_path.write_text(
        "# COMPOSE_FILE=docker-compose.yml:docker/gpu.nvidia.yml\n"
        "# COMPOSE_FILE=docker-compose.yml;docker/gpu.nvidia.yml    #(Windows)\n",
        encoding="utf-8",
    )

    assert setup_module.configure_docker_target("nvidia", "linux", env_path=str(env_path)) == "updated"

    text = env_path.read_text(encoding="utf-8")
    assert "COMPOSE_FILE=docker-compose.yml:docker/gpu.nvidia.yml" in text


def test_configure_docker_target_nvidia_windows_uses_semicolon(tmp_path):
    setup_module = _load_setup_module()
    env_path = tmp_path / ".env"
    env_path.write_text(
        "# COMPOSE_FILE=docker-compose.yml:docker/gpu.nvidia.yml\n"
        "# COMPOSE_FILE=docker-compose.yml;docker/gpu.nvidia.yml    #(Windows)\n",
        encoding="utf-8",
    )

    assert setup_module.configure_docker_target("nvidia", "windows", env_path=str(env_path)) == "updated"

    text = env_path.read_text(encoding="utf-8")
    assert "COMPOSE_FILE=docker-compose.yml;docker/gpu.nvidia.yml" in text


def test_configure_docker_target_amd_sets_render_gid(tmp_path):
    setup_module = _load_setup_module()
    env_path = tmp_path / ".env"
    env_path.write_text(
        "# COMPOSE_FILE=docker-compose.yml:docker/gpu.amd.yml\n"
        "# RENDER_GID=989\n",
        encoding="utf-8",
    )

    assert setup_module.configure_docker_target(
        "amd", "linux", env_path=str(env_path), render_gid="1234"
    ) == "updated"

    text = env_path.read_text(encoding="utf-8")
    assert "COMPOSE_FILE=docker-compose.yml:docker/gpu.amd.yml" in text
    assert "RENDER_GID=1234" in text


def test_configure_docker_target_cpu_leaves_env_unchanged(tmp_path):
    setup_module = _load_setup_module()
    env_path = tmp_path / ".env"
    original = "# COMPOSE_FILE=docker-compose.yml:docker/gpu.nvidia.yml\n# RENDER_GID=989\n"
    env_path.write_text(original, encoding="utf-8")

    assert setup_module.configure_docker_target("cpu", "linux", env_path=str(env_path)) == "skipped"

    assert env_path.read_text(encoding="utf-8") == original


def test_optional_requirements_lists_installable_lines(tmp_path):
    setup_module = _load_setup_module()
    optional = tmp_path / "requirements-optional.txt"
    optional.write_text(
        "# comment\n"
        "\n"
        "faster-whisper\n"
        "markitdown[docx]==0.1.5\n",
        encoding="utf-8",
    )

    assert setup_module._optional_requirements(str(optional)) == [
        "faster-whisper",
        "markitdown[docx]==0.1.5",
    ]


def test_configure_optional_requirements_sets_docker_env(tmp_path):
    setup_module = _load_setup_module()
    env_path = tmp_path / ".env"
    env_path.write_text("# INSTALL_OPTIONAL=false\n", encoding="utf-8")

    assert setup_module.configure_optional_requirements(
        True, "docker", env_path=str(env_path)
    ) == "updated"

    assert "INSTALL_OPTIONAL=true" in env_path.read_text(encoding="utf-8")


def test_prompt_choice_accepts_punctuated_number(monkeypatch):
    setup_module = _load_setup_module()
    monkeypatch.setattr(builtins, "input", lambda _prompt: "2.")

    choice = setup_module._prompt_choice(
        "Docker setup target:",
        [("cpu", "CPU only"), ("nvidia", "CPU + NVIDIA GPU")],
        "cpu",
    )

    assert choice == "nvidia"


def test_prompt_choice_accepts_compact_label(monkeypatch):
    setup_module = _load_setup_module()
    monkeypatch.setattr(builtins, "input", lambda _prompt: "cpu nvidia gpu")

    choice = setup_module._prompt_choice(
        "Docker setup target:",
        [("cpu", "CPU only"), ("nvidia", "CPU + NVIDIA GPU")],
        "cpu",
    )

    assert choice == "nvidia"
