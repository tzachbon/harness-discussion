"""Read timed events from EventKit; calendar access is requested only explicitly."""

from datetime import datetime, timezone

from EventKit import EKEntityTypeEvent, EKEventStore
from Foundation import NSDate

from harness_discussion.meetings import Event


def status():
    value = EKEventStore.authorizationStatusForEntityType_(EKEntityTypeEvent)
    return {0: 'not_determined', 1: 'restricted', 2: 'denied', 3: 'allowed', 4: 'denied'}.get(value, 'denied')


def request(then):
    store = EKEventStore.alloc().init()

    def completed(granted, error):
        then(bool(granted))

    if hasattr(store, 'requestFullAccessToEventsWithCompletion_'):
        store.requestFullAccessToEventsWithCompletion_(completed)
    else:
        store.requestAccessToEntityType_completion_(EKEntityTypeEvent, completed)


def events(start, end):
    store = EKEventStore.alloc().init()
    start_date = NSDate.dateWithTimeIntervalSince1970_(start.timestamp())
    end_date = NSDate.dateWithTimeIntervalSince1970_(end.timestamp())
    predicate = store.predicateForEventsWithStartDate_endDate_calendars_(start_date, end_date, None)
    return [Event(event.title() or '',
                  datetime.fromtimestamp(event.startDate().timeIntervalSince1970(), timezone.utc),
                  datetime.fromtimestamp(event.endDate().timeIntervalSince1970(), timezone.utc),
                  'eventkit')
            for event in store.eventsMatchingPredicate_(predicate) if not event.isAllDay()]
