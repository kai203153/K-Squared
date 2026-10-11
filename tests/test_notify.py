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

    def alert_args(self, prompt, button="Got it"):
        with mock.patch("subprocess.run", return_value=mock.Mock(stdout=button + "\n")) as run, \
                mock.patch("subprocess.Popen") as popen, \
                mock.patch("threading.Thread", side_effect=lambda target, **kw: mock.Mock(start=target)):
            f = finding(prompt)
            notify.notify_finding(f)
        return f, run.call_args[0][0], popen

    def test_low_is_an_informational_alert_that_closes_itself(self):
        _, cmd, popen = self.alert_args(SCENARIOS["unconfirmed"]["prompt"])
        self.assertEqual(cmd[-4], "Align: you might be operating on an assumption")
        self.assertIn("The payments API exists and provides what this work needs.", cmd[-3])
        self.assertEqual((cmd[-2], cmd[-1]), ("informational", "12"))
        popen.assert_not_called()

    def test_medium_is_a_warning_alert_with_show_me(self):
        f, cmd, popen = self.alert_args(SCENARIOS["duplicate"]["prompt"], button="Show me")
        self.assertEqual(cmd[-4], "Align: check existing code first")
        self.assertIn("validate_event already exists", cmd[-3])
        self.assertEqual(cmd[-2], "warning")
        self.assertEqual(popen.call_args[0][0], ["open", f"http://127.0.0.1:8765/#f-{f['id']}"])

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

    def test_popups_wait_like_a_real_investigation(self):
        with mock.patch.dict(os.environ, {"ALIGN_POPUP_DELAY": "5"}):
            delays = {sev: [notify.delay_for({"severity": sev}) for _ in range(50)] for sev in ("LOW", "HIGH")}
            with mock.patch("threading.Timer") as timer:
                notify.notify_finding({"severity": "LOW"})
        self.assertTrue(all(3 <= d <= 5 for d in delays["LOW"]))
        self.assertTrue(all(5.5 <= d <= 7.5 for d in delays["HIGH"]))
        self.assertGreater(timer.call_args[0][0], 0)
        self.assertTrue(timer.return_value.daemon)

    def test_off_means_silent(self):
        with mock.patch.dict(os.environ, {"ALIGN_NOTIFY": "off"}), mock.patch("subprocess.Popen") as popen:
            notify.notify_finding({"severity": "HIGH"})
        popen.assert_not_called()
