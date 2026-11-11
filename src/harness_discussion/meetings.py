"""Meeting files and transcript presentation; raw transcript parts stay on disk."""
import json
import os
import re
import secrets
import shutil
from dataclasses import asdict, dataclass, replace
from datetime import datetime
from difflib import SequenceMatcher
from pathlib import Path

from . import settings

DIR = Path(settings.LOCAL_DIR) / 'meetings'
STATES = ('recording', 'transcribing', 'noting', 'done', 'interrupted', 'failed_transcript', 'failed_notes')


class Error(Exception):
    pass


@dataclass(frozen=True)
class Event:
    title: str
    start: datetime
    end: datetime
    source: str


@dataclass(frozen=True)
class Part:
    track: str
    start: float
    end: float
    text: str = ''
    source: str = ''
    silent: bool = False


@dataclass
class Meeting:
    id: str
    title: str
    app: str
    started_at: str
    ended_at: str | None
    state: str
    error: str = ''
    event: dict | None = None
    auto: bool = False

    @property
    def folder(self) -> Path:
        return _folder(self.id)


def _folder(meeting_id):
    root = DIR.resolve()
    folder = (root / meeting_id).resolve()
    if folder.parent != root or folder == root:
        raise Error('Invalid meeting id')
    return folder


def new(title: str, app: str, event: Event | None, auto: bool, now: datetime | None = None) -> Meeting:
    now = now or datetime.now().astimezone()
    DIR.mkdir(parents=True, exist_ok=True)
    while True:
        meeting_id = now.strftime('%Y-%m-%dT%H-%M-%S-') + secrets.token_hex(2)
        try:
            _folder(meeting_id).mkdir()
            break
        except FileExistsError:
            continue
    event_data = None if event is None else {'title': event.title, 'start': event.start.isoformat(),
                                              'end': event.end.isoformat(), 'source': event.source}
    meeting = Meeting(meeting_id, title, app, now.isoformat(), None, 'recording', event=event_data, auto=auto)
    save(meeting)
    return meeting


def load(meeting_id: str) -> Meeting:
    try:
        data = json.loads((_folder(meeting_id) / 'meeting.json').read_text(encoding='utf-8'))
        data.pop('version', None)
        data.pop('notes_source', None)
        meeting = Meeting(**data)
        if meeting.id != meeting_id:
            raise ValueError
        return meeting
    except (OSError, ValueError, TypeError, AttributeError) as exc:
        raise Error('Unable to load meeting') from None


def save(m: Meeting) -> None:
    folder = m.folder
    data = {'version': 1, **asdict(m)}
    tmp = folder / 'meeting.json.tmp'
    with tmp.open('w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, folder / 'meeting.json')


def all() -> list[Meeting]:
    if not DIR.exists():
        return []
    result = []
    for folder in DIR.iterdir():
        if folder.is_dir():
            try:
                result.append(load(folder.name))
            except Error:
                continue
    return sorted(result, key=lambda m: m.started_at, reverse=True)


def delete(meeting_id: str) -> None:
    shutil.rmtree(_folder(meeting_id))


def add_part(meeting_id: str, part: Part) -> None:
    with (_folder(meeting_id) / 'transcript.jsonl').open('a', encoding='utf-8') as f:
        f.write(json.dumps(asdict(part), ensure_ascii=False) + '\n')
        f.flush()
        os.fsync(f.fileno())


def parts(meeting_id: str) -> list[Part]:
    path = _folder(meeting_id) / 'transcript.jsonl'
    if not path.exists():
        return []
    result = {}
    with path.open(encoding='utf-8') as f:
        for line in f:
            try:
                part = Part(**json.loads(line))
            except (ValueError, TypeError):
                continue
            result[(part.track, part.start)] = part
    return list(result.values())


def transcribed_until(meeting_id: str, track: str) -> float:
    return max((part.end for part in parts(meeting_id) if part.track == track), default=0)


def unfinished() -> list[Meeting]:
    return [m for m in all() if m.state in ('recording', 'interrupted', 'transcribing', 'noting')]


def size_bytes() -> int:
    return sum(path.stat().st_size for path in DIR.rglob('*') if path.is_file()) if DIR.exists() else 0


def remove_echo(parts: list[Part]) -> list[Part]:
    result = []
    them = [part for part in parts if part.track == 'them']
    for part in parts:
        if part.track != 'you':
            result.append(part)
            continue
        words = list(re.finditer(r'\b\w+\b', part.text, re.UNICODE))
        normalized = [word.group().lower() for word in words]
        removed = set()
        for other in them:
            if other.start > part.end + 3 or other.end < part.start - 3:
                continue
            other_words = re.findall(r'\b\w+\b', other.text.lower(), re.UNICODE)
            for block in SequenceMatcher(None, normalized, other_words, autojunk=False).get_matching_blocks():
                if block.size >= 4:
                    removed.update(range(block.a, block.a + block.size))
        if not removed:
            result.append(part)
        elif len(words) - len(removed) >= 2:
            chunks = []
            for i, word in enumerate(words):
                if i not in removed:
                    end = words[i + 1].start() if i + 1 < len(words) else len(part.text)
                    chunks.append(part.text[word.start():end])
            result.append(replace(part, text=''.join(chunks).strip()))
    return result


def lines(parts: list[Part]) -> list[tuple[float, str, str]]:
    return sorted((p.start, 'You' if p.track == 'you' else 'Them', p.text)
                  for p in remove_echo(parts) if p.text and not p.silent)


def notes(meeting_id: str) -> str:
    try:
        return (_folder(meeting_id) / 'notes.md').read_text(encoding='utf-8')
    except FileNotFoundError:
        return ''


def save_notes(meeting_id: str, markdown: str) -> None:
    (_folder(meeting_id) / 'notes.md').write_text(markdown, encoding='utf-8')


def markdown(m: Meeting, notes_md: str, lines, transcript: bool = True) -> str:
    start = datetime.fromisoformat(m.started_at)
    end = datetime.fromisoformat(m.ended_at) if m.ended_at else None
    date = start.strftime('%Y-%m-%d %H:%M')
    if end:
        date += f"–{end:%H:%M} ({int((end - start).total_seconds() // 60)} min)"
    result = [f'# {m.title}', '', f'- Date: {date}']
    if m.event:
        event_start = datetime.fromisoformat(m.event['start'])
        event_end = datetime.fromisoformat(m.event['end'])
        result.append(f"- Calendar: {m.event['title']}, {event_start:%H:%M}–{event_end:%H:%M}")
    result.append(f'- App: {m.app}')
    if notes_md.strip():
        result.extend(['', notes_md.strip()])
    if transcript:
        result.extend(['', '## Transcript'])
        for seconds, label, text in lines:
            seconds = int(seconds)
            result.append(f'**{seconds // 60:02d}:{seconds % 60:02d} {label}:** {text}')
    return '\n'.join(result) + '\n'
