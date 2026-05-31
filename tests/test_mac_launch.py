import importlib.machinery
import importlib.util
import sys
import tempfile
import unittest
from pathlib import Path


def load_mac_launch():
    root = Path(__file__).resolve().parents[1]
    path = root / "scripts" / "odysseus-mac"
    loader = importlib.machinery.SourceFileLoader("odysseus_mac", str(path))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    loader.exec_module(module)
    return module


mac_launch = load_mac_launch()


class MacLaunchTests(unittest.TestCase):
    def test_parse_and_classify_python_versions(self):
        self.assertEqual(mac_launch.parse_python_version("Python 3.11.15"), (3, 11, 15))
        self.assertEqual(mac_launch.parse_python_version("Python 3.14"), (3, 14, 0))
        self.assertEqual(mac_launch.classify_python_version((3, 11, 15)), "supported")
        self.assertEqual(mac_launch.classify_python_version((3, 12, 9)), "supported")
        self.assertEqual(mac_launch.classify_python_version((3, 14, 5)), "too_new")
        self.assertEqual(mac_launch.classify_python_version((3, 10, 14)), "too_old")

    def test_set_env_updates_and_appends(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / ".env.example").write_text("AUTH_ENABLED=true\nPUID=1000\n")
            text = mac_launch.set_env(root, {"PUID": "501", "PGID": "20"}).read_text()
        self.assertIn("AUTH_ENABLED=true\n", text)
        self.assertIn("PUID=501\n", text)
        self.assertIn("PGID=20\n", text)
        self.assertNotIn("PUID=1000", text)

    def test_split_bind_accepts_bare_port_and_host_port(self):
        self.assertEqual(mac_launch.split_bind("7001", "127.0.0.1", 7000), ("127.0.0.1", 7001))
        self.assertEqual(mac_launch.split_bind("0.0.0.0:18080", "127.0.0.1", 8080), ("0.0.0.0", 18080))

    def test_prepare_env_persists_explicit_port_and_searxng_base_url(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / ".env.example").write_text("SEARXNG_INSTANCE=http://localhost:8080\n")
            env_path = mac_launch.prepare_env(
                root,
                docker_port=7777,
                searx_bind="127.0.0.1:18080",
                auto_searx=False,
                uid=501,
                gid=20,
            )
            text = env_path.read_text()
        self.assertIn("ODYSSEUS_BIND=7777\n", text)
        self.assertIn("PUID=501\n", text)
        self.assertIn("PGID=20\n", text)
        self.assertIn("SEARXNG_BIND=127.0.0.1:18080\n", text)
        self.assertIn("SEARXNG_BASE_URL=http://127.0.0.1:18080/\n", text)

    def test_prepare_env_sets_native_searxng_instance(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / ".env.example").write_text(
                "SEARXNG_INSTANCE=http://localhost:8080\n"
                "SEARXNG_BIND=127.0.0.1:18080\n"
            )
            text = mac_launch.prepare_env(root, services=True, auto_searx=False, uid=501, gid=20).read_text()
        self.assertIn("SEARXNG_INSTANCE=http://127.0.0.1:18080\n", text)
        self.assertIn("SEARXNG_BASE_URL=http://127.0.0.1:18080/\n", text)


if __name__ == "__main__":
    unittest.main()
