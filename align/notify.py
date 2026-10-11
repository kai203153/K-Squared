"""Desktop pop-ups for new findings (ALIGN_NOTIFY=auto|on|off).

- LOW / MEDIUM: a banner. The engineer is told there is an assumption worth double-checking.
- HIGH: an alert dialog. "Explore reason" opens the finding on the dashboard; "Ask manager"
  is a placeholder until escalation is built (nothing is ever sent automatically).
"""
import shutil
import subprocess
import sys
import threading

from .config import settings

EXPLORE, ASK_MANAGER, DISMISS = "Explore reason", "Ask manager", "Dismiss"

BANNER = ["-e", "on run argv",
          "-e", "display notification (item 2 of argv) with title (item 1 of argv) subtitle (item 3 of argv)",
          "-e", "end run"]
DIALOG = ["-e", "on run argv",
          "-e", f'set r to display dialog (item 2 of argv) with title (item 1 of argv) buttons '
                f'{{"{DISMISS}", "{ASK_MANAGER}", "{EXPLORE}"}} default button "{EXPLORE}" '
                f'with icon caution giving up after 300',
          "-e", "return button returned of r",
          "-e", "end run"]
COMING_SOON = ["-e", "on run argv",
               "-e", 'display dialog (item 1 of argv) with title "Align" buttons {"OK"} default button "OK"',
               "-e", "end run"]


def enabled() -> bool:
    mode = settings().notify
    if mode == "off":
        return False
    supported = sys.platform == "darwin" or shutil.which("notify-send") is not None
    return supported if mode == "auto" else True


def _popen(cmd: list[str]) -> None:
    try:
        subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except OSError:
        pass


def banner(title: str, subtitle: str, message: str) -> None:
    # All text goes through argv, never into the AppleScript source.
    if sys.platform == "darwin":
        _popen(["osascript", *BANNER, title, message[:240], subtitle])
    else:
        _popen(["notify-send", title, f"{subtitle}\n{message[:240]}"])


def finding_url(finding: dict) -> str:
    return f"{settings().url}/#f-{finding['id']}"


def high_alert(finding: dict, message: str) -> None:
    """Blocking dialog with actions, run on a daemon thread so analysis never waits on it."""
    def run() -> None:
        try:
            p = subprocess.run(["osascript", *DIALOG, "Align: high-risk assumption", message[:900]],
                               capture_output=True, text=True, timeout=330)
        except (OSError, subprocess.TimeoutExpired):
            return
        choice = p.stdout.strip()
        if choice == EXPLORE:
            _popen(["open", finding_url(finding)])
        elif choice == ASK_MANAGER:
            _popen(["osascript", *COMING_SOON,
                    "Asking your manager is coming soon. Nothing was sent. For now, use "
                    "\"Draft a question\" on the Align dashboard to write one yourself."])

    if sys.platform == "darwin":
        threading.Thread(target=run, name="align-alert", daemon=True).start()
    else:
        _popen(["notify-send", "--urgency=critical", "Align: high-risk assumption", message[:240]])


def notify_finding(finding: dict) -> None:
    """Pop up a new finding, styled by severity. Fire-and-forget; never raises."""
    if not enabled():
        return
    if finding["severity"] == "HIGH":
        high_alert(finding, (
            f"If this assumption goes through, the work conflicts with confirmed {finding['fact_id']}:\n"
            f"“{finding['fact_quote']}”\n\n"
            f"You said: “{finding['event_quote']}”\n\n"
            f"Estimated rework at risk: {finding['impact_min']:.1f}–{finding['impact_max']:.1f} h"))
        return
    title = ("Align: assumption conflicts with code" if finding["severity"] == "MEDIUM"
             else "Align: assumption to double-check")
    subject = finding["assumption"] or finding["event_quote"]
    banner(title, f"{finding['severity']} · {finding['fact_source']}",
           f"There's an assumption Align can't confirm: {subject}. {finding['explanation']}")
