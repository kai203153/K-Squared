import os
import sys
from unittest import mock

from align import engine, notify

from .helpers import SCENARIOS, AlignTestCase


def finding(prompt):
    eid = engine.ingest({"task_id": "DE-104", "session": "n", "kind": "prompt", "content": prompt})
    with mock.patch("align.notify.notify_finding") as popup:
        engine.analyze_event(eid)
    return popup.call_args[0][0]


@mock.patch.object(sys, "platform", "darwin")
class NotifyTests(AlignTestCase):
    env = {"ALIGN_NOTIFY": "on"}

    def test_low_is_a_banner_saying_the_assumption_cant_be_confirmed(self):
        with mock.patch("subprocess.Popen") as popen:
            notify.notify_finding(finding(SCENARIOS["duplicate"]["prompt"]))
        cmd = popen.call_args[0][0]
        self.assertIn("display notification (item 2 of argv) with title (item 1 of argv) subtitle (item 3 of argv)", cmd)
        self.assertEqual(cmd[-3], "Align: assumption to double-check")
        self.assertIn("assumption Align can't confirm", cmd[-2])
        self.assertTrue(cmd[-1].startswith("LOW"))

    def test_high_is_a_dialog_with_explore_and_ask_manager(self):
        f = finding(SCENARIOS["positive"]["prompt"])
        result = mock.Mock(stdout="Explore reason\n")
        with mock.patch("subprocess.run", return_value=result) as run, mock.patch("subprocess.Popen") as popen, \
                mock.patch("threading.Thread", side_effect=lambda target, **kw: mock.Mock(start=target)):
            notify.notify_finding(f)
        script = " ".join(run.call_args[0][0])
        self.assertIn('buttons {"Dismiss", "Ask manager", "Explore reason"}', script)
        self.assertIn("If this assumption goes through", run.call_args[0][0][-1])
        self.assertEqual(popen.call_args[0][0], ["open", f"http://127.0.0.1:8765/#f-{f['id']}"])

    def test_ask_manager_is_a_placeholder_that_sends_nothing(self):
        f = finding(SCENARIOS["positive"]["prompt"])
        with mock.patch("subprocess.run", return_value=mock.Mock(stdout="Ask manager\n")), \
                mock.patch("subprocess.Popen") as popen, \
                mock.patch("threading.Thread", side_effect=lambda target, **kw: mock.Mock(start=target)):
            notify.notify_finding(f)
        self.assertIn("coming soon", popen.call_args[0][0][-1])
        self.assertEqual(engine.open_findings("DE-104")[0]["status"], "open")

    def test_off_means_silent(self):
        with mock.patch.dict(os.environ, {"ALIGN_NOTIFY": "off"}), mock.patch("subprocess.Popen") as popen:
            notify.notify_finding({"severity": "HIGH"})
        popen.assert_not_called()
