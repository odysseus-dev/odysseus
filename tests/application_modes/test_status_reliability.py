"""Status, provider and project reads: shared, bounded, and never stuck on the expensive patch check."""
import asyncio
import sys
import threading
import time
import unittest
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))


class RouteGuardCacheTests(unittest.TestCase):
    def component(self, tmp):
        from src.hermes_webapp_component import WebappComponent
        root = Path(tmp) / 'root'
        (root / 'managed' / 'webapp').mkdir(parents=True)
        (root / 'managed' / 'webapp' / 'component.json').write_text('{}')
        component = WebappComponent(root, application_root=ROOT)
        self.calls = 0

        def slow(config):
            self.calls += 1
            time.sleep(0.2)
            return 'applied'
        component.route_guard_state = slow
        return component

    def test_concurrent_status_reads_run_one_check_and_reuse_it(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            component = self.component(tmp)
            config = {'origin': 'managed', 'agent': 'a', 'release': 'r', 'revisions': {'webapp': 'x'}}
            results = []
            threads = [threading.Thread(target=lambda: results.append(component.cached_route_guard_state(config))) for _ in range(8)]
            for t in threads:
                t.start()
            for t in threads:
                t.join()
            component.cached_route_guard_state(config)
            self.assertEqual(results, ['applied'] * 8)
            self.assertEqual(self.calls, 1)

    def test_config_change_and_explicit_invalidation_recompute(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            component = self.component(tmp)
            config = {'origin': 'managed', 'agent': 'a', 'release': 'r', 'revisions': {}}
            component.cached_route_guard_state(config)
            component.cached_route_guard_state({**config, 'release': 'r2'})            # another release: new key
            self.assertEqual(self.calls, 2)
            component.invalidate_route_guard()
            component.cached_route_guard_state({**config, 'release': 'r2'})
            self.assertEqual(self.calls, 3)
            component.config_path.write_text('{"changed": 1}')                          # a config write (rollback, roots, ensure) changes the key
            time.sleep(0.01)
            component.cached_route_guard_state({**config, 'release': 'r2'})
            self.assertEqual(self.calls, 4)

    def test_unknown_is_never_remembered_and_validation_stays_uncached(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            component = self.component(tmp)
            states = iter(['unknown', 'applied'])
            component.route_guard_state = lambda config: next(states)
            config = {'origin': 'managed', 'agent': 'a', 'release': 'r', 'revisions': {}}
            self.assertEqual(component.cached_route_guard_state(config), 'unknown')
            self.assertEqual(component.cached_route_guard_state(config), 'applied')
        import inspect
        from src.hermes_webapp_component import WebappComponent
        self.assertNotIn('cached_route_guard_state', inspect.getsource(WebappComponent.validate))


class ModeServiceStatusTests(unittest.TestCase):
    def service(self):
        from src.application_modes import ModeService
        service = ModeService(SimpleNamespace(component_root='x', ownership='managed', url='http://127.0.0.1:1/', backend='gateway'))
        self.probes = 0

        def probe():
            self.probes += 1
            time.sleep(0.1)
            return {'enabled': True, 'state': 'ready'}
        service._managed_status = probe
        return service

    def test_concurrent_reads_share_one_probe_and_report_its_cost(self):
        service = self.service()
        results = []
        threads = [threading.Thread(target=lambda: results.append(service.status())) for _ in range(6)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        self.assertEqual(self.probes, 1)
        self.assertTrue(all(r['state'] == 'ready' and r['probe_ms'] >= 90 for r in results))

    def test_lifecycle_invalidates(self):
        service = self.service()
        service.status()
        service._invalidate_status()
        service.status()
        self.assertEqual(self.probes, 2)


class LocalApiSharingTests(unittest.TestCase):
    def test_identical_reads_deduplicate_and_survive_one_waiter_leaving(self):
        from src.hermes_local_api import LocalProjectsApi
        api = LocalProjectsApi(lambda: None)
        calls = []

        def work():
            calls.append(1)
            time.sleep(0.2)
            return {'projects': []}

        async def scenario():
            api._slots = asyncio.Semaphore(4)
            first = asyncio.ensure_future(api._shared(('k',), work, 3.0))
            second = asyncio.ensure_future(api._shared(('k',), work, 3.0))
            await asyncio.sleep(0.05)
            first.cancel()                                           # one waiter gives up: the shared read must continue for the other
            value = await second
            again = await api._shared(('k',), work, 3.0)             # served from the short cache
            other = await api._shared(('other',), work, 3.0)         # a different key is not blocked or merged
            return value, again, other

        value, again, other = asyncio.run(scenario())
        self.assertEqual(value, {'projects': []})
        self.assertEqual(again, value)
        self.assertEqual(len(calls), 2)


class ProviderFailureCacheTests(unittest.TestCase):
    def test_unreachable_hermes_is_rechecked_soon_but_a_good_answer_is_kept(self):
        from src.provider_sync import ProviderSync
        clock = [0.0]
        states = iter(['offline', 'ready', 'ready'])
        fetches = []

        def fetch():
            fetches.append(1)
            state = next(states)
            return ({'providers': []} if state == 'ready' else None), state
        sync = ProviderSync(lambda: {}, lambda data: None, lambda: [], fetch, None, None, probe=lambda url: ('offline', ()), clock=lambda: clock[0])
        self.assertEqual(sync.snapshot()['applications']['hermes'], 'offline')
        clock[0] = 1.0
        sync.snapshot()
        self.assertEqual(len(fetches), 1)                            # within the short failure window
        clock[0] = 4.0
        self.assertEqual(sync.snapshot()['applications']['hermes'], 'ready')
        clock[0] = 15.0
        sync.snapshot()
        self.assertEqual(len(fetches), 2)                            # a good answer lives the full interval


if __name__ == '__main__':
    unittest.main()
