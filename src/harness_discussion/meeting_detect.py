"""Debounce call detection using monotonic time and calendar event windows."""
from datetime import timedelta

POLL_S, HOLD_S, END_S, REARM_S, ASK_S, END_WAIT_S = 2, 8, 20, 60, 30, 120
CALL_APPS = {
    "zoom.exe": "Zoom", "us.zoom.xos": "Zoom", "ms-teams.exe": "Teams", "msteams_": "Teams",
    "teams.exe": "Teams", "com.microsoft.teams": "Teams", "webex.exe": "Webex",
    "ciscocollabhost.exe": "Webex", "com.cisco.webexmeetingsapp": "Webex", "cisco-systems.spark": "Webex",
    "slack.exe": "Slack", "com.tinyspeck.slackmacgap": "Slack", "discord.exe": "Discord",
    "com.hnc.discord": "Discord", "com.apple.facetime": "FaceTime",
}
BROWSERS = {
    "chrome.exe": "Chrome", "com.google.chrome": "Chrome", "msedge.exe": "Edge",
    "com.microsoft.edgemac": "Edge", "firefox.exe": "Firefox", "org.mozilla.firefox": "Firefox",
    "brave.exe": "Brave", "com.brave.browser": "Brave", "arc.exe": "Arc",
    "company.thebrowser.browser": "Arc", "com.apple.safari": "Safari", "com.apple.webkit": "Safari",
}


def app_name(app_id):
    return next((name for prefix, name in (CALL_APPS | BROWSERS).items() if app_id.lower().startswith(prefix)), None)


def current_event(events, now):
    return max((event for event in events if event.start - timedelta(minutes=5) <= now < event.end
                and event.end - event.start <= timedelta(hours=4)), key=lambda event: event.start, default=None)


class Detector:
    def __init__(self):
        self.app = None
        self._candidate = self._since = self._absent = self._missing = None
        self._suppressed = self._end_sent = False
        self._recording = None

    def dismissed(self, now):
        self._suppressed = True

    def kept(self, now):
        self._end_sent = True

    def update(self, now, users, in_event, recording):
        candidate = next((u for u in sorted(users) if app_name(u)), None)
        if candidate is None and in_event:
            candidate = next(iter(sorted(users)), None)
        if candidate != self._candidate:
            self._candidate, self._since = candidate, now
        if candidate is None:
            if self._absent is None:
                self._absent = now
            if now - self._absent >= REARM_S:
                self._suppressed = False
        else:
            self._absent = None
        if recording != self._recording:
            self._recording, self._missing, self._end_sent = recording, None, False
        if recording:
            self._suppressed = True
            if recording != "manual":
                if recording in users:
                    self._missing, self._end_sent = None, False
                elif self._missing is None:
                    self._missing = now
                elif not self._end_sent and now - self._missing >= END_S:
                    self._end_sent = True
                    return "ended"
        elif candidate is not None and not self._suppressed and now - self._since >= HOLD_S:
            self.app, self._suppressed = candidate, True
            return "ask"
        return None
