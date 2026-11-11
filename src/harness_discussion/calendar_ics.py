"""Read HTTPS calendar feeds and expand timed events within a bounded window."""
import re
import urllib.request
from datetime import datetime, timedelta, timezone
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from dateutil.rrule import rrulestr

from .meetings import Event

MAX_BYTES = 5_000_000


def normalize_url(url):
    url = url.strip()
    if url.lower().startswith("webcal://"):
        url = "https://" + url[9:]
    parsed = urlsplit(url)
    if parsed.scheme.lower() != "https" or not parsed.hostname:
        raise ValueError("Calendar URL must use HTTPS.")
    return url


class _HTTPSRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        if urlsplit(newurl).scheme.lower() != "https":
            raise ValueError("Calendar redirect must use HTTPS.")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def fetch(url, timeout=15):
    request = urllib.request.Request(normalize_url(url), headers={"User-Agent": "HarnessDiscussion"})
    with urllib.request.build_opener(_HTTPSRedirect()).open(request, timeout=timeout) as response:
        data = response.read(MAX_BYTES + 1)
    if len(data) > MAX_BYTES:
        raise ValueError("Calendar feed is too large.")
    return data


def parse(data, start, end):
    lines = re.sub(r"\r?\n[ \t]", "", data.decode("utf-8-sig")).splitlines()
    zones, events, component, zone, standard = {}, [], None, None, False
    for line in lines:
        if line == "BEGIN:VTIMEZONE":
            zone = {}
        elif line == "END:VTIMEZONE":
            if "TZID" in zone and "TZOFFSETTO" in zone:
                zones[zone["TZID"]] = zone["TZOFFSETTO"]
            zone = None
        elif zone is not None:
            if line == "BEGIN:STANDARD":
                standard = True
            elif line == "END:STANDARD":
                standard = False
            elif ":" in line:
                key, value = line.split(":", 1)
                if key == "TZID" or (standard and key == "TZOFFSETTO" and key not in zone):
                    zone[key] = value
        elif line == "BEGIN:VEVENT":
            component = {}
        elif line == "END:VEVENT" and component is not None:
            events.append(component)
            component = None
        elif component is not None and ":" in line:
            key, value = line.split(":", 1)
            parts = key.split(";")
            params = dict(p.split("=", 1) for p in parts[1:] if "=" in p)
            component.setdefault(parts[0].upper(), []).append((params, value))

    def dt(item):
        params, value = item
        if params.get("VALUE") == "DATE" or re.fullmatch(r"\d{8}", value):
            return None
        utc = value.endswith("Z")
        value = value.removesuffix("Z")
        parsed = datetime.strptime(value, "%Y%m%dT%H%M%S" if len(value) == 15 else "%Y%m%dT%H%M")
        tzid = params.get("TZID", "").strip('"')
        if utc:
            tz = timezone.utc
        elif tzid:
            try:
                tz = ZoneInfo(tzid)
            except ZoneInfoNotFoundError:
                offset = zones[tzid]
                sign = -1 if offset[0] == "-" else 1
                tz = timezone(sign * timedelta(hours=int(offset[1:3]), minutes=int(offset[3:5])))
        else:
            tz = parsed.astimezone().tzinfo
        return parsed.replace(tzinfo=tz)

    def first(event, key, default=None):
        return event.get(key, [({}, default)])[0][1]

    def duration(event, beginning):
        if "DTEND" in event:
            finish = dt(event["DTEND"][0])
            return finish - beginning if finish else timedelta(0)
        value = first(event, "DURATION", "")
        match = re.fullmatch(r"(?P<sign>[+-])?P(?:(?P<weeks>\d+)W)?(?:(?P<days>\d+)D)?(?:T(?:(?P<hours>\d+)H)?(?:(?P<minutes>\d+)M)?(?:(?P<seconds>\d+)S)?)?", value)
        if not match:
            return timedelta(0)
        values = {k: int(v or 0) for k, v in match.groupdict().items() if k != "sign"}
        return timedelta(**values) * (-1 if match.group("sign") == "-" else 1)

    def title(event):
        value = first(event, "SUMMARY", "") or "Busy"
        return re.sub(r"\\([nN,;\\])", lambda m: "\n" if m[1].lower() == "n" else m[1], value)

    overrides = {}
    for event in events:
        if "RECURRENCE-ID" in event:
            rid = dt(event["RECURRENCE-ID"][0])
            overrides[(first(event, "UID"), rid)] = event
    result = []
    def add(event, beginning, length):
        if first(event, "STATUS", "").upper() != "CANCELLED" and beginning < end and beginning + length > start:
            result.append(Event(title(event), beginning, beginning + length, "ics"))
    for event in events:
        if "RECURRENCE-ID" in event or "DTSTART" not in event or first(event, "STATUS", "").upper() == "CANCELLED":
            continue
        beginning = dt(event["DTSTART"][0])
        if beginning is None:
            continue
        length = duration(event, beginning)
        uid = first(event, "UID")
        exceptions = {dt((params, value)) for params, values in event.get("EXDATE", []) for value in values.split(",")}
        if "RRULE" in event:
            instances = rrulestr(first(event, "RRULE"), dtstart=beginning).between(start - length, end, inc=True)
        else:
            instances = [beginning]
        for instance in instances:
            if (uid, instance) in overrides:
                continue
            if instance not in exceptions:
                add(event, instance, length)
    for event in overrides.values():
        if "DTSTART" in event:
            beginning = dt(event["DTSTART"][0])
            if beginning is not None:
                add(event, beginning, duration(event, beginning))
    return sorted(result, key=lambda event: event.start)

