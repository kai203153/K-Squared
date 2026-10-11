import json
import os
import shutil
import tempfile
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from align import context, db
from align.config import ROOT
from align.knowledge import store
from align.knowledge.scan import scan

EXAMPLE = ROOT / "examples" / "de-104"
SCENARIOS = json.loads((EXAMPLE / "scenarios.json").read_text())


class AlignTestCase(unittest.TestCase):
    env: dict = {}

    def setUp(self):
        self.home = Path(tempfile.mkdtemp(prefix="align-test-"))
        self.addCleanup(shutil.rmtree, self.home, True)
        patcher = mock.patch.dict(os.environ, {"ALIGN_HOME": str(self.home), "ALIGN_DETECTOR": "rules",
                                               "ALIGN_GATE": "off", "ALIGN_NOTIFY": "off", **self.env})
        patcher.start()
        self.addCleanup(patcher.stop)
        os.environ.pop("ALIGN_TOKEN", None)
        db.migrate()
        context.import_dir(EXAMPLE)
        name, facts = scan(EXAMPLE / "demo_repo", working_tree=True)
        store().replace_code(name, facts)


class FakeClaude:
    """Stands in for anthropic.Anthropic(); returns a canned structured-output response."""

    def __init__(self, payload=None, error=None, stop_reason="end_turn"):
        self.payload, self.error, self.stop_reason, self.calls = payload, error, stop_reason, []
        self.beta = SimpleNamespace(messages=SimpleNamespace(create=self._create))

    def _create(self, **kwargs):
        self.calls.append(kwargs)
        if self.error:
            raise self.error
        return SimpleNamespace(stop_reason=self.stop_reason,
                               content=[SimpleNamespace(type="text", text=json.dumps(self.payload))])


def serve(testcase):
    """Start the real server on an ephemeral port for the duration of a test."""
    from align.server import make_server

    server = make_server(port=0)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    testcase.addCleanup(server.server_close)
    testcase.addCleanup(server.shutdown)
    return f"http://127.0.0.1:{server.server_address[1]}"
