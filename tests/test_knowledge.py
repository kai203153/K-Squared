import json
import subprocess
import tempfile
from pathlib import Path

from align.knowledge import Fact, store, terms
from align.knowledge.scan import scan

from .helpers import AlignTestCase


def git(repo, *args):
    subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True,
                   env={"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t", "GIT_COMMITTER_NAME": "t",
                        "GIT_COMMITTER_EMAIL": "t@t", "PATH": "/usr/bin:/bin:/usr/local/bin:/opt/homebrew/bin"})


class StoreTests(AlignTestCase):
    def test_only_verified_facts_are_stored(self):
        with self.assertRaises(ValueError):
            store().upsert([Fact(id="X", kind="convention", subject="s", statement="t", source="x", verified_by="")])
        with self.assertRaises(ValueError):
            store().upsert([Fact(id="X", kind="rumor", subject="s", statement="t", source="x", verified_by="me")])
        store().upsert([Fact(id="X", kind="convention", subject="logging", statement="Use structlog.",
                             source="wiki", verified_by="staff eng")])
        self.assertEqual(store().get("X").verified_by, "staff eng")

    def test_replace_code_drops_removed_code(self):
        before = store().stats()["by_kind"]["component"]
        store().replace_code("demo_repo", [])
        self.assertNotIn("component", store().stats()["by_kind"])
        self.assertGreater(before, 0)
        self.assertIsNotNone(store().get("ADR-12"))  # Decisions are untouched.

    def test_search_ranks_by_shared_terms(self):
        self.assertEqual(store().search("retry with backoff")[0][1].subject, "with_backoff")
        self.assertEqual(store().search(""), [])

    def test_scope_limits_facts_to_tasks(self):
        self.assertTrue(any(f.id == "ADR-12" for f in store().facts("DE-104")))
        self.assertFalse(any(f.id == "ADR-12" for f in store().facts("OTHER")))

    def test_terms(self):
        self.assertEqual(terms("validateEvent EventSchema retry_helper"), {"valid", "event", "schem", "retry", "helpe"})


class ScanTests(AlignTestCase):
    def make_repo(self, files: dict) -> Path:
        repo = Path(tempfile.mkdtemp(dir=self.home)) / "svc"
        for name, text in files.items():
            (repo / name).parent.mkdir(parents=True, exist_ok=True)
            (repo / name).write_text(text)
        return repo

    def test_extractors(self):
        repo = self.make_repo({
            "src/client.ts": "export class PaymentsClient {}\nexport async function charge() {}\n",
            "infra/stack.yaml": "Resources:\n  Fn:\n    Type: AWS::Lambda::Function\n",
            "package.json": json.dumps({"dependencies": {"axios": "^1.6.0"}}),
            "pyproject.toml": '[project]\ndependencies = ["httpx>=0.27"]\n',
            "app/models.py": "class _Private: pass\nclass Order:\n    pass\n",
            "app/tests/test_models.py": "class TestOrder: pass\n",
            "web/client.spec.ts": "export class Fake {}\n",
        })
        _, facts = scan(repo, working_tree=True)
        subjects = {f.subject: f for f in facts}
        self.assertEqual(set(subjects), {"PaymentsClient", "charge", "AWS Lambda Function", "axios", "httpx", "Order"})
        self.assertEqual(subjects["PaymentsClient"].evidence, "export class PaymentsClient {}")
        self.assertEqual(subjects["Order"].source, "app/models.py:2")
        self.assertTrue(all(f.verified_by.endswith("@working-tree") for f in facts))

    def test_git_scan_reads_only_committed_code(self):
        repo = self.make_repo({"svc.py": "def committed():\n    pass\n"})
        git(repo, "init", "-q")
        git(repo, "add", ".")
        git(repo, "commit", "-qm", "init")
        (repo / "svc.py").write_text("def committed():\n    pass\n\ndef unmerged_work():\n    pass\n")
        (repo / "new.py").write_text("def draft():\n    pass\n")
        _, facts = scan(repo)
        self.assertEqual([f.subject for f in facts], ["committed"])
        self.assertRegex(facts[0].verified_by, r"^production-code:svc@HEAD:[0-9a-f]{10}$")

    def test_git_scan_of_subdirectory(self):
        top = self.make_repo({"svc/api.py": "def handler():\n    pass\n", "other.py": "def elsewhere():\n    pass\n"})
        git(top, "init", "-q")
        git(top, "add", ".")
        git(top, "commit", "-qm", "init")
        _, facts = scan(top / "svc")
        self.assertEqual([(f.subject, f.source) for f in facts], [("handler", "api.py:1")])

    def test_bad_ref_is_an_error(self):
        repo = self.make_repo({"a.py": "def a(): pass\n"})
        git(repo, "init", "-q")
        with self.assertRaises(ValueError):
            scan(repo, ref="main")
