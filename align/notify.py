"""Desktop pop-ups for new findings (ALIGN_NOTIFY=auto|on|off).

Each severity looks different, and none needs macOS notification permission (banners from
scripts are often silently blocked, which is fatal in a live demo):
- LOW: a small informational alert, "You might be operating on an assumption", that closes
  itself after a few seconds.
- MEDIUM: a warning alert pointing at existing code, with "Show me" to open the finding.
- HIGH: a caution dialog. "Explore reason" opens the finding on the dashboard; "Ask manager"
  is a placeholder until escalation is built (nothing is ever sent automatically).
"""
import random
import shutil
import subprocess
import sys
import threading

from .config import settings

EXPLORE, ASK_MANAGER, DISMISS = "Explore reason", "Ask manager", "Dismiss"
SHOW_ME, GOT_IT = "Show me", "Got it"
LOW_SECONDS, MEDIUM_SECONDS = 12, 20

BANNER = ["-e", "on run argv",
          "-e", "display notification (item 2 of argv) with title (item 1 of argv) subtitle (item 3 of argv)",
          "-e", "end run"]
DIALOG = ["-e", "on run argv",
          "-e", f'set r to display dialog (item 2 of argv) with title (item 1 of argv) buttons '
                f'{{"{DISMISS}", "{ASK_MANAGER}", "{EXPLORE}"}} default button "{EXPLORE}" '
                f'with icon caution giving up after 300',
          "-e", "return button returned of r",
          "-e", "end run"]
# display alert: title, message, style (informational|warning), seconds; returns the button pressed.
ALERT = ["-e", "on run argv",
         "-e", "set s to informational",
         "-e", 'if item 3 of argv is "warning" then set s to warning',
         "-e", f'if item 3 of argv is "warning" then set b to {{"{GOT_IT}", "{SHOW_ME}"}}',
         "-e", f'if item 3 of argv is not "warning" then set b to {{"{GOT_IT}"}}',
         "-e", "set r to display alert (item 1 of argv) message (item 2 of argv) as s buttons b "
               f'default button "{GOT_IT}" giving up after (item 4 of argv as integer)',
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


def alert(finding: dict, title: str, message: str, style: str, seconds: int) -> None:
    """Non-blocking alert window on macOS; a banner elsewhere."""
    if sys.platform != "darwin":
        banner(title, finding["severity"], message)
        return

    def run() -> None:
        try:
            p = subprocess.run(["osascript", *ALERT, title, message[:600], style, str(seconds)],
                               capture_output=True, text=True, timeout=seconds + 30)
        except (OSError, subprocess.TimeoutExpired):
            return
        if p.stdout.strip() == SHOW_ME:
            _popen(["open", finding_url(finding)])

    threading.Thread(target=run, name="align-alert", daemon=True).start()


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


# Bigger findings take a little longer, as a deeper check would.
SEVERITY_EXTRA = {"LOW": -1.0, "MEDIUM": 0.0, "HIGH": 1.5}


def delay_for(finding: dict) -> float:
    base = settings().popup_delay
    if base <= 0:
        return 0.0
    return max(0.0, base + SEVERITY_EXTRA.get(finding["severity"], 0.0) + random.uniform(-1.0, 1.0))


def notify_finding(finding: dict) -> None:
    """Pop up a new finding, styled by severity, after a short pause. Fire-and-forget."""
    if not enabled():
        return
    delay = delay_for(finding)
    if delay == 0:
        show(finding)
        return
    timer = threading.Timer(delay, show, args=(finding,))
    timer.daemon = True
    timer.start()


def show(finding: dict) -> None:
    if finding["severity"] == "HIGH":
        high_alert(finding, (
            f"If this assumption goes through, the work conflicts with confirmed {finding['fact_id']}:\n"
            f"“{finding['fact_quote']}”\n\n"
            f"You said: “{finding['event_quote']}”\n\n"
            f"Estimated rework at risk: {finding['impact_min']:.1f}–{finding['impact_max']:.1f} h"))
        return
    subject = (finding["assumption"] or finding["event_quote"]).rstrip(".")
    if finding["severity"] == "MEDIUM":
        alert(finding, "Align: check existing code first",
              f"{finding['explanation']}\n\nYou may be assuming: {subject}.", "warning", MEDIUM_SECONDS)
    else:
        alert(finding, "Align: you might be operating on an assumption",
              f"{subject}.\n\nNothing verified confirms this. Confirm it before building on it.",
              "informational", LOW_SECONDS)
