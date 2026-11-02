"""Sampling boundary regression checks; live request replay is also required."""
import ast
import unittest
from pathlib import Path
from src.generation_sampling import validate_temperature

ROOT = Path(__file__).resolve().parents[1]


class SamplingContractTests(unittest.TestCase):
    def test_requested_sampling_and_greedy_values_preserved(self):
        for value in (0, .2, 1., 1.5):
            self.assertEqual(validate_temperature(value), value)

    def test_invalid_values_rejected(self):
        for value in (None, True, '1.0', -1., float('nan'), float('inf')):
            with self.subTest(value=value), self.assertRaises(ValueError):
                validate_temperature(value)

    def test_preview_wrapper_forwards_temperature(self):
        tree = ast.parse((ROOT / 'src/agent_loop.py').read_text())
        calls = [n for n in ast.walk(tree) if isinstance(n, ast.Call)
                 and isinstance(n.func, ast.Name) and n.func.id == 'stream_preview']
        self.assertTrue(calls)
        for call in calls:
            values = [k.value for k in call.keywords if k.arg == 'temperature']
            self.assertEqual(len(values), 1)
            self.assertIsInstance(values[0], ast.Name)
            self.assertEqual(values[0].id, 'temperature')

    def test_preview_request_uses_validated_temperature(self):
        tree = ast.parse((ROOT / 'src/clean_agent_preview.py').read_text())
        function = next(n for n in tree.body if isinstance(n, ast.AsyncFunctionDef) and n.name == 'stream_preview')
        values = [v for n in ast.walk(function) if isinstance(n, ast.Dict)
                  for k, v in zip(n.keys, n.values) if isinstance(k, ast.Constant) and k.value == 'temperature']
        self.assertTrue(values)
        self.assertTrue(all(isinstance(v, ast.Name) and v.id == 'temperature' for v in values))


if __name__ == '__main__':
    unittest.main()
