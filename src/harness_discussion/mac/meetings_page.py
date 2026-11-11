"""Meetings settings; calendar links are kept out of logs."""
import AppKit
from Foundation import NSURL

from .. import meeting_copy as copy, meetings
from . import ui


class MeetingsPage:
    def _meetings_page(self):
        from .settings_ui import WIDTH, _card, _divider, _note, _spacer

        notes, self.meetings_switch = ui.option(copy.MEETING_NOTES, copy.RECORD_MAC, self.cfg['meetings'],
                                                self._meetings_toggled, WIDTH)
        ask, self.meeting_ask = ui.option(copy.ASK_WHEN_CALL, copy.ASK_HELP, self.cfg['meeting_ask'],
                                           lambda on: self._set('meeting_ask', on), WIDTH)
        self.eventkit = ui.checkbox(copy.USE_CALENDAR, self.cfg['meeting_eventkit'], self._eventkit_toggled)
        self.eventkit_status = ui.label('', 11, secondary=True)
        self.eventkit_button = ui.button('', self._eventkit_access)
        eventkit = ui.stack([self.eventkit, _spacer(), self.eventkit_status, self.eventkit_button], vertical=False)
        self.url_hidden = AppKit.NSSecureTextField.alloc().init()
        self.url_visible = AppKit.NSTextField.alloc().init()
        for field in (self.url_hidden, self.url_visible):
            field.setStringValue_(self.cfg['meeting_ics_url'])
            field.setAccessibilityLabel_(copy.CALENDAR_LINK)
            field.widthAnchor().constraintEqualToConstant_(WIDTH - 150).setActive_(True)
            ui.bind(field, lambda s: self._set('meeting_ics_url', str(s.stringValue())))
        self.url_visible.setHidden_(True)
        self.url_show = ui.checkbox(copy.SHOW, False, self._show_url)
        self.calendar_status_label = ui.label('', 11, secondary=True, width=WIDTH)
        link = ui.stack([self.url_hidden, self.url_visible, self.url_show, ui.button(copy.TEST, self._test_calendar)],
                        vertical=False)
        self.audio_status = ui.label('', 12, width=WIDTH - 130)
        self.audio_fix = ui.button(copy.FIX_THIS, lambda: self.app.host.screen_permission_guide())
        audio = ui.stack([self.audio_status, _spacer(), self.audio_fix], vertical=False)
        self.storage_label = ui.label('', 12)
        storage = ui.stack([self.storage_label, _spacer(), ui.button('Open Meeting Notes', lambda: self.app._meeting_open())],
                           vertical=False)
        self._refresh_meetings()
        return [_card([notes, _divider(), ask]), ui.label(copy.CALENDAR, 13, bold=True),
                _card([eventkit, ui.label(copy.CALENDAR_LINK, 12), link, self.calendar_status_label,
                       ui.label(copy.CALENDAR_HELP, 11, secondary=True, width=WIDTH)]),
                ui.label(copy.SYSTEM_AUDIO, 13, bold=True), _card([audio]),
                ui.label(copy.STORAGE, 13, bold=True), _card([storage]), _note(copy.SETTINGS_NOTE)]

    def _meetings_toggled(self, on):
        getattr(self.app, 'set_meetings', lambda _on: None)(on)
        self.cfg['meetings'] = self.app.cfg['meetings']
        self.meetings_switch.setState_(int(self.cfg['meetings']))

    def _show_url(self, on):
        source, target = (self.url_hidden, self.url_visible) if on else (self.url_visible, self.url_hidden)
        target.setStringValue_(source.stringValue())
        source.setHidden_(True)
        target.setHidden_(False)

    def _test_calendar(self):
        field = self.url_visible if self.url_show.state() else self.url_hidden
        self._set('meeting_ics_url', str(field.stringValue()))
        self.calendar_status_label.setStringValue_(copy.CHECKING)
        getattr(self.app, 'refresh_meeting_calendar', lambda: None)()
        if hasattr(self, '_calendar_timer'):
            self._calendar_timer.invalidate()
        self._calendar_timer = ui.timer(0.5, self._refresh_calendar_status, repeats=True)

    def _refresh_calendar_status(self):
        self.calendar_status_label.setStringValue_(getattr(self.app, 'calendar_status', ''))

    def _eventkit_toggled(self, on):
        self._set('meeting_eventkit', on)
        if on and getattr(self.app.host, 'calendar', lambda: None)() and self.app.host.calendar().status() == 'not_determined':
            self._eventkit_access()

    def _eventkit_access(self):
        cal = getattr(self.app.host, 'calendar', lambda: None)()
        if cal is None:
            return
        if cal.status() == 'not_determined':
            target = ui.target(lambda _: self._refresh_meetings())
            cal.request(lambda _: target.performSelectorOnMainThread_withObject_waitUntilDone_('run:', None, False))
        else:
            AppKit.NSWorkspace.sharedWorkspace().openURL_(NSURL.URLWithString_(
                'x-apple.systempreferences:com.apple.preference.security?Privacy_Calendars'))

    def _refresh_meetings(self):
        cal = getattr(self.app.host, 'calendar', lambda: None)() if hasattr(self.app, 'set_meetings') else None
        status = cal.status() if cal else 'not_determined'
        self.eventkit_status.setStringValue_({'allowed': 'Connected', 'not_determined': 'Not allowed',
                                              'restricted': 'Restricted', 'denied': 'Denied'}[status])
        self.eventkit_button.setHidden_(status == 'allowed')
        self.eventkit_button.setTitle_(copy.ALLOW_ACCESS if status == 'not_determined' else copy.OPEN_SYSTEM_SETTINGS)
        ready = getattr(self.app.host, 'system_audio_status', lambda: '')() == 'ready'
        self.audio_status.setStringValue_('Ready' if ready else 'Needed · ' + copy.SYSTEM_AUDIO_OFF)
        self.audio_fix.setHidden_(ready)
        self.storage_label.setStringValue_(copy.STORAGE_SIZE.format(n=len(meetings.all()),
                                           size=f'{meetings.size_bytes() / 1024 / 1024:.0f} MB'))
        self._refresh_calendar_status()
