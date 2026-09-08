"""Inline frontmatter lists must preserve quoted values across save/load cycles."""

import importlib.util
from pathlib import Path
import sys
import unittest


# Load the dependency-free formatter without starting the services package.
_PATH = Path(__file__).resolve().parents[1] / "services" / "memory" / "skill_format.py"
_SPEC = importlib.util.spec_from_file_location("_skill_list_format_under_test", _PATH)
_FORMAT = importlib.util.module_from_spec(_SPEC)
sys.modules[_SPEC.name] = _FORMAT
_SPEC.loader.exec_module(_FORMAT)


class FrontmatterListEscapesTests(unittest.TestCase):
    def test_skill_tags_survive_repeated_save_load_cycles(self):
        tags = ['review "release, notes', 'use "search", then summarize', 'publish']
        skill = _FORMAT.Skill(name="release-review", tags=tags)
        for _ in range(5):
            skill = _FORMAT.Skill.from_markdown(skill.to_markdown())
            self.assertEqual(skill.tags, tags)

    def test_escaped_quotes_preserve_each_list_field(self):
        for field in ("tags", "platforms", "requires_toolsets", "fallback_for_toolsets"):
            with self.subTest(field=field):
                values = ['review "release, notes', 'publish']
                skill = _FORMAT.Skill(name="release-review", **{field: values})
                restored = _FORMAT.Skill.from_markdown(skill.to_markdown())
                self.assertEqual(getattr(restored, field), values)

    def test_backslash_parity_does_not_hide_quotes_or_list_separators(self):
        for count in range(1, 5):
            with self.subTest(backslashes=count):
                values = ['review ' + '\\' * count + '"release, notes', 'path, ' + '\\' * count, 'publish']
                text = _FORMAT.emit_frontmatter({"tags": values})
                parsed, _ = _FORMAT.parse_frontmatter(f"---\n{text}\n---\n")
                self.assertEqual(parsed["tags"], values)

    def test_single_quoted_backslashes_remain_literal(self):
        text = "---\ntags: ['C:\\', next]\n---\n"
        parsed, _ = _FORMAT.parse_frontmatter(text)
        self.assertEqual(parsed["tags"], ["C:\\", "next"])

    def test_nested_lists_preserve_quoted_brackets_and_commas(self):
        values = [['review "], notes', 'publish'], 'archive']
        text = _FORMAT.emit_frontmatter({"tags": values})
        parsed, _ = _FORMAT.parse_frontmatter(f"---\n{text}\n---\n")
        self.assertEqual(parsed["tags"], values)


if __name__ == "__main__":
    unittest.main()
