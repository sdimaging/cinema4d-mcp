"""Archive retrieval tests; these do not certify agent behavior or C4D UI."""
import importlib.util
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location('skill_knowledge',
    ROOT / 'skills/cinema4d-mcp/scripts/knowledge.py')
knowledge = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(knowledge)


class KnowledgeTests(unittest.TestCase):
    def test_complete_numbered_archive(self):
        text = (ROOT / 'docs/c4d_2026_api_gotchas.md').read_text(encoding='utf-8')
        index = knowledge.entries(text)
        self.assertEqual(set(index), set(range(1, max(index)+1)))
        self.assertGreaterEqual(len(index), 135)
        self.assertTrue(all(body in text for _, body in index.values()))

    def test_sections_include_subheadings_not_next_entry(self):
        text = 'Intro\n## 2. Two\nBody\n### Detail\nMore\n## Unnumbered\nOther\n## 1. One\nLast'
        index = knowledge.entries(text)
        self.assertEqual(index[2][1], '## 2. Two\nBody\n### Detail\nMore')
        self.assertEqual(index[1][1], '## 1. One\nLast')

    def test_duplicate_ids_rejected(self):
        with self.assertRaises(ValueError):
            knowledge.entries('## 1. First\nA\n## 1. Second\nB')

    def test_explicit_repo(self):
        self.assertEqual(knowledge.resolve_repo(str(ROOT)), ROOT)

    def test_explicit_bad_repo_does_not_silently_fallback(self):
        with self.assertRaises(ValueError):
            knowledge.resolve_repo(str(ROOT / '__missing_skill_test_checkout__'))


if __name__ == '__main__':
    unittest.main()
