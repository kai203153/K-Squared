import tempfile
from pathlib import Path

from align import context

from .helpers import AlignTestCase


class ContextTests(AlignTestCase):
    def test_frontmatter_lists_comments_and_body(self):
        meta, body = context.parse_frontmatter(
            "---\nid: X-1\n# comment\napplies_to: A, B\nforbid: a|b\nforbid: c\n---\nBody text.\n")
        self.assertEqual(meta["id"], "X-1")
        self.assertEqual(meta["applies_to"], ["A", "B"])
        self.assertEqual(meta["forbid"], ["a|b", "c"])
        self.assertEqual(body, "Body text.")

    def test_example_import(self):
        self.assertTrue(context.task_exists("DE-104"))
        ids = [r["id"] for r in context.records_for("DE-104", authoritative_only=True)]
        self.assertEqual(ids, ["ADR-12", "REQ-5"])
        self.assertEqual(context.records_for("OTHER"), [])

    def _import(self, files: dict) -> None:
        d = Path(tempfile.mkdtemp(dir=self.home))
        for name, text in files.items():
            (d / name).write_text(text)
        context.import_dir(d)

    def test_only_confirmed_current_records_are_authoritative(self):
        self._import({
            "p.md": "---\nid: P-1\nstatus: proposed\napplies_to: *\n---\nProposed rule.",
            "old.md": "---\nid: OLD-1\nstatus: confirmed\napplies_to: DE-104\nvalid_until: 2000-01-01\n---\nExpired.",
            "notes.md": "Plain markdown without frontmatter is ignored.",
        })
        all_ids = {r["id"] for r in context.records_for("DE-104")}
        enforced = {r["id"] for r in context.records_for("DE-104", authoritative_only=True)}
        self.assertTrue({"P-1", "OLD-1"} <= all_ids)
        self.assertFalse({"P-1", "OLD-1"} & enforced)

    def test_invalid_status_and_regex_rejected(self):
        with self.assertRaises(ValueError):
            self._import({"bad.md": "---\nid: B\nstatus: maybe\n---\nx"})
        with self.assertRaises(Exception):
            self._import({"bad.md": "---\nid: B\nstatus: confirmed\nforbid: (unclosed\n---\nx"})
