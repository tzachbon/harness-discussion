"""Deterministic UIA documents and guarded edit boundaries for Windows tests."""
from contextlib import ExitStack, contextmanager
from types import SimpleNamespace
from unittest.mock import Mock, patch

from harness_discussion.win import clipboard

UNSUPPORTED = object()

@contextmanager
def native_host(window):
    from harness_discussion.win import selection
    client = SimpleNamespace(IUIAutomation=SimpleNamespace(CheckNotSupported=lambda value: value is UNSUPPORTED,
                                                            CreatePropertyCondition=lambda *args: args))
    native_class = lambda hwnd, buf, size: setattr(buf, "value", "Chrome_RenderWidgetHostHWND") or 27
    with patch.object(selection.uia, "GetForegroundWindow", return_value=42), \
            patch.object(selection.uia, "GetAncestor", return_value=42), \
            patch.object(selection.uia, "ControlFromHandle", return_value=window), \
            patch.object(selection.uia, "ControlsAreSame", side_effect=lambda left, right: left is right), \
            patch.object(selection.u32, "GetClassNameW", side_effect=native_class), \
            patch.object(selection.automation._AutomationClient, "instance", return_value=client):
        yield


class Document:
    """A deterministic UIA provider, including independent selection and document state."""
    def __init__(self, text="Left old right", start=5, end=8):
        self.text, self.start, self.end = text, start, end
        self.IsPassword, self.writable, self.identity = False, True, [1, 2]
        self.FrameworkId = "Win32"
        self.Element = SimpleNamespace(GetCurrentPropertyValueEx=lambda prop, suppress: self.IsPassword)

    def GetParentControl(self):
        return None

    def GetRuntimeId(self):
        return self.identity

    def GetPattern(self, pattern):
        from harness_discussion.win import edit
        if pattern == edit.uia.PatternId.TextPattern:
            return self
        if pattern == edit.uia.PatternId.ValuePattern:
            return SimpleNamespace(IsReadOnly=not self.writable)

    @property
    def DocumentRange(self):
        return Range(self, 0, len(self.text))

    def GetSelection(self):
        return [Range(self, self.start, self.end)]


class Range:
    def __init__(self, document, start, end):
        self.document, self.start, self.end = document, start, end

    def GetText(self, maximum):
        text = self.document.text[self.start:self.end]
        return text if maximum < 0 else text[:maximum]

    def Clone(self):
        return Range(self.document, self.start, self.end)

    @property
    def textRange(self):
        return self

    def GetChildren(self):
        return []

    def CompareEndpoints(self, endpoint, other, other_endpoint):
        return (self.start if endpoint == 0 else self.end) - (other.start if other_endpoint == 0 else other.end)

    def MoveEndpointByRange(self, endpoint, other, other_endpoint, waitTime=0):
        value = other.start if other_endpoint == 0 else other.end
        if endpoint == 0:
            self.start = value
        else:
            self.end = value
        return True

    def MoveEndpointByUnit(self, endpoint, unit, count, waitTime=0):
        if endpoint != 0 or count != -1 or self.start == 0:
            return 0
        self.start -= 1
        return -1

    def Select(self, waitTime=0):
        self.document.start, self.document.end = self.start, self.end
        return True


@contextmanager
def target(document):
    from harness_discussion.win import edit
    native = Mock()
    native.GetForegroundWindow.return_value = 42
    native.GetClassNameW.side_effect = lambda hwnd, buf, n: setattr(buf, "value", "Notepad") or 7
    def pid(hwnd, out):
        out._obj.value = 9
        return 1
    native.GetWindowThreadProcessId.side_effect = pid
    with ExitStack() as stack:
        stack.enter_context(patch.object(edit, "u32", native))
        stack.enter_context(patch.object(edit.uia, "GetFocusedControl", return_value=document))
        snap = stack.enter_context(patch.object(edit.clipboard, "snapshot_with_sequence", return_value=([], 10)))
        restore = stack.enter_context(patch.object(edit.clipboard, "restore", return_value=11))
        cleanup = stack.enter_context(patch.object(edit.clipboard, "PreparedRestore")).return_value.__enter__.return_value
        cleanup.apply.return_value = 12
        stack.enter_context(patch.object(edit.clipboard, "sequence", return_value=11))
        key = Mock(return_value=False)
        def chord(vk, on_attempt=None):
            if on_attempt is not None:
                on_attempt()
            return key(vk)
        stack.enter_context(patch.object(edit.keys, "chord", side_effect=chord))
        yield native, key, snap, cleanup.apply
