"""Native meeting list and notes viewer."""
from datetime import datetime

import AppKit
from Foundation import NSObject, NSMutableAttributedString, NSAttributedString

from .. import meeting_copy as copy, meetings
from . import ui


def _dates(m):
    start = datetime.fromisoformat(m.started_at).astimezone()
    end = datetime.fromisoformat(m.ended_at).astimezone() if m.ended_at else None
    length = f"{int((end - start).total_seconds() // 60)} min" if end else "—"
    return start, end, length


def _button(title, fn):
    button = ui.button(title, fn)
    button.setAccessibilityLabel_(title)
    return button


def _text():
    scroll = AppKit.NSScrollView.alloc().init()
    scroll.setHasVerticalScroller_(True)
    scroll.setBorderType_(AppKit.NSBezelBorder)
    view = AppKit.NSTextView.alloc().init()
    view.setEditable_(False)
    view.setSelectable_(True)
    view.setFont_(AppKit.NSFont.systemFontOfSize_(13))
    view.setTextContainerInset_((10, 10))
    view.setVerticallyResizable_(True)
    view.setAutoresizingMask_(AppKit.NSViewWidthSizable)
    scroll.setDocumentView_(view)
    return scroll, view


class _Table(NSObject):
    def numberOfRowsInTableView_(self, table):
        return len(self.owner.items)

    def tableView_objectValueForTableColumn_row_(self, table, column, row):
        m = self.owner.items[row]
        start, _, length = _dates(m)
        glyph = {'recording': '●', 'done': '✓', 'failed_transcript': '!', 'failed_notes': '!'}.get(m.state, '◷')
        state = '' if m.state == 'done' else ' · ' + m.state.replace('_', ' ')  # the ✓ glyph already says done
        return f"{glyph}  {m.title}\n{start:%a %d %b %H:%M} · {length}{state}"

    def tableViewSelectionDidChange_(self, notification):
        row = self.owner.table.selectedRow()
        if 0 <= row < len(self.owner.items):
            self.owner.select(self.owner.items[row].id)


class NotesWindow:
    def __init__(self, app, meeting_id=None):
        self.app, self.host = app, app.host
        self.items, self.meeting = [], None
        self.delegate = _Table.alloc().init()
        self.delegate.owner = self
        self.table = AppKit.NSTableView.alloc().init()
        col = AppKit.NSTableColumn.alloc().initWithIdentifier_('meeting')
        col.setWidth_(230)
        self.table.addTableColumn_(col)
        self.table.setHeaderView_(None)
        self.table.setRowHeight_(45)
        self.table.setDataSource_(self.delegate)
        self.table.setDelegate_(self.delegate)
        self.table.setAccessibilityLabel_(copy.WINDOW_TITLE)
        left = AppKit.NSScrollView.alloc().init()
        left.setDocumentView_(self.table)
        left.setHasVerticalScroller_(True)
        self.title = ui.label('', 18, bold=True)
        self.event = ui.label('', 12, secondary=True)
        self.meta = ui.label('', 12, secondary=True)
        self.state = ui.label('', 12, secondary=True)
        self.retry_transcript = _button(copy.TRANSCRIBE_AGAIN, lambda: self._retry(True))
        self.retry_notes = _button(copy.MAKE_NOTES_AGAIN, lambda: self._retry(False))
        self.tabs = AppKit.NSSegmentedControl.segmentedControlWithLabels_trackingMode_target_action_(
            [copy.NOTES, copy.TRANSCRIPT], AppKit.NSSegmentSwitchTrackingSelectOne, None, None)
        self.tabs.setAccessibilityLabel_('Notes and transcript')
        ui.bind(self.tabs, lambda _: self._tab())
        self.tabs.setSelectedSegment_(0)
        self.scroll, self.text = _text()
        self.text.setAccessibilityLabel_(copy.NOTES)
        self.copy_button = _button(copy.COPY_NOTES, self._copy)
        self.export_button = _button(copy.EXPORT_MARKDOWN, self._export)
        self.finder_button = _button(copy.SHOW_IN_FINDER, self._reveal)
        self.delete_button = _button(copy.DELETE, self._delete)
        self.delete_button.setToolTip_(copy.STOP_FIRST)
        right = ui.stack([self.title, self.event, self.meta,
                          ui.stack([self.state, self.retry_transcript, self.retry_notes], vertical=False),
                          self.tabs, self.scroll,
                          ui.stack([self.copy_button, self.export_button, self.finder_button, self.delete_button], vertical=False)])
        right.setEdgeInsets_((14, 14, 14, 14))
        self.scroll.widthAnchor().constraintEqualToConstant_(560).setActive_(True)
        self.scroll.heightAnchor().constraintEqualToConstant_(300).setActive_(True)
        left.widthAnchor().constraintEqualToConstant_(230).setActive_(True)
        split = AppKit.NSSplitView.alloc().init()
        split.setVertical_(True)
        split.setDividerStyle_(AppKit.NSSplitViewDividerStyleThin)
        split.addSubview_(left)
        split.addSubview_(right)
        style = AppKit.NSWindowStyleMaskTitled | AppKit.NSWindowStyleMaskClosable | AppKit.NSWindowStyleMaskResizable
        self.win = AppKit.NSWindow.alloc().initWithContentRect_styleMask_backing_defer_(
            ((0, 0), (830, 500)), style, AppKit.NSBackingStoreBuffered, False)
        self.win.setTitle_(copy.WINDOW_TITLE)
        self.win.setReleasedWhenClosed_(False)
        self.win.setContentView_(split)
        self.win.center()
        self.focus(meeting_id)
        self.timer = ui.timer(1, self._refresh, repeats=True)

    @property
    def alive(self):
        return bool(self.win.isVisible())

    def focus(self, meeting_id):
        self._refresh(meeting_id)
        ui.show(self.win)

    def _refresh(self, meeting_id=None):
        if not self.alive and hasattr(self, 'timer'):
            self.timer.invalidate()
            return
        selected = meeting_id or (self.meeting.id if self.meeting else None)
        self.items = meetings.all()
        self.table.reloadData()
        index = next((i for i, m in enumerate(self.items) if m.id == selected), 0)
        if self.items:
            self.table.selectRowIndexes_byExtendingSelection_(AppKit.NSIndexSet.indexSetWithIndex_(index), False)
            self.select(self.items[index].id)
        else:
            self.meeting = None
            self.title.setStringValue_(copy.EMPTY_LIST)
            for field in (self.event, self.meta, self.state):
                field.setStringValue_('')
            self.text.setString_('')
            for button in (self.copy_button, self.export_button, self.finder_button, self.delete_button):
                button.setEnabled_(False)
            self.retry_transcript.setHidden_(True)
            self.retry_notes.setHidden_(True)

    def select(self, meeting_id):
        self.meeting = m = meetings.load(meeting_id)
        start, end, length = _dates(m)
        self.title.setStringValue_(m.title)
        event = m.event
        if event:
            es = datetime.fromisoformat(event['start']).astimezone()
            ee = datetime.fromisoformat(event['end']).astimezone()
            self.event.setStringValue_(f"📅 {event['title']} · {es:%a %d %b %Y, %H:%M}–{ee:%H:%M}")
        else:
            self.event.setStringValue_('')
        self.event.setHidden_(not bool(event))
        times = f"{start:%H:%M}–{end:%H:%M}" if end else f"{start:%H:%M}"
        self.meta.setStringValue_(' · '.join(filter(None, [m.app, times, length])))
        done, total = self.app.meeting_progress(m.id)
        states = {'recording': copy.STATE_RECORDING, 'transcribing': copy.STATE_TRANSCRIBING.format(done=done, total=total),
                  'noting': copy.STATE_MAKING, 'interrupted': copy.STATE_INTERRUPTED,
                  'failed_transcript': copy.STATE_TRANSCRIPTION_FAILED.format(reason=m.error),
                  'failed_notes': copy.STATE_NOTES_FAILED.format(reason=m.error),
                  'done': copy.STATE_PARTIAL if m.error else ''}
        self.state.setStringValue_(states.get(m.state, ''))
        self.retry_transcript.setHidden_(not (m.state == 'failed_transcript' or m.state == 'done' and m.error))
        self.retry_notes.setHidden_(m.state != 'failed_notes')
        busy = self.app.meeting_busy(m.id) or m.state in ('recording', 'interrupted', 'transcribing', 'noting')
        self.delete_button.setEnabled_(not busy)
        for button in (self.copy_button, self.export_button, self.finder_button):
            button.setEnabled_(True)
        self._tab()

    def _lines(self):
        return meetings.lines(meetings.remove_echo(meetings.parts(self.meeting.id)))

    def _tab(self):
        if not self.meeting:
            return
        transcript = self.tabs.selectedSegment() == 1
        self.text.setAccessibilityLabel_(copy.TRANSCRIPT if transcript else copy.NOTES)
        result = NSMutableAttributedString.alloc().init()
        font = AppKit.NSFont.systemFontOfSize_(13)

        def append(text, **attrs):
            attributes = {AppKit.NSFontAttributeName: font, AppKit.NSForegroundColorAttributeName: AppKit.NSColor.labelColor()}
            attributes.update(attrs)
            result.appendAttributedString_(NSAttributedString.alloc().initWithString_attributes_(text, attributes))

        if transcript:
            for index, (t, who, body) in enumerate(self._lines()):
                if index:
                    append('\n')
                append(f'{int(t)//60:02d}:{int(t)%60:02d}  ', **{AppKit.NSForegroundColorAttributeName: AppKit.NSColor.secondaryLabelColor()})
                append(who, **{AppKit.NSForegroundColorAttributeName: AppKit.NSColor.systemBlueColor() if who == 'You' else AppKit.NSColor.secondaryLabelColor()})
                append(f'  {body}')
        else:
            text = meetings.notes(self.meeting.id) or copy.NOTES_PLACEHOLDER
            for index, line in enumerate(text.split('\n')):
                if index:
                    append('\n')
                stripped = line.lstrip('#')
                if len(stripped) < len(line) and stripped.startswith(' '):
                    style = AppKit.NSMutableParagraphStyle.alloc().init()
                    style.setParagraphSpacingBefore_(4)
                    append(stripped.lstrip(), **{AppKit.NSFontAttributeName: AppKit.NSFont.boldSystemFontOfSize_(15), AppKit.NSParagraphStyleAttributeName: style})
                else:
                    append(line)
        if not self.text.textStorage().isEqualToAttributedString_(result):
            self.text.textStorage().setAttributedString_(result)

    def _markdown(self, transcript):
        return meetings.markdown(self.meeting, meetings.notes(self.meeting.id), self._lines(), transcript=transcript)

    def _copy(self):
        self.host.copy_text(self._markdown(False))
        self.host.notify(copy.NOTES_COPIED)

    def _export(self):
        self.host.save_text(copy.export_name(self.meeting), self._markdown(True))

    def _reveal(self):
        self.host.reveal(self.meeting.folder)

    def _delete(self):
        m = self.meeting
        if not self.app.meeting_busy(m.id) and self.host.confirm(copy.DELETE, copy.DELETE_CONFIRM, ok=copy.DELETE_OK):
            if self.app.delete_meeting(m.id):
                self.meeting = None
                self._refresh()

    def _retry(self, transcript):
        (self.app.redo_transcript if transcript else self.app.redo_notes)(self.meeting.id)
        self._refresh()
