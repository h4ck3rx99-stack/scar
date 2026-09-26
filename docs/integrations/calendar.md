# Calendar

SCAR's calendar tools (`calendar.list`, `calendar.create`, `calendar.update`,
`calendar.delete`, `calendar.import_ics`, `calendar.export_ics`) work with three
providers:

| Provider | Needs | Notes |
|---|---|---|
| `local` | nothing | SQLite table in SCAR's data folder; always available |
| `google` | Google OAuth client + `scar auth google` | primary calendar, Calendar API v3 |
| `outlook` | Microsoft app registration + `scar auth microsoft` | Graph `/me/events`, `/me/calendarView` |

`calendar_provider = auto` (default) uses Google when signed in, else Outlook when
signed in, else the local calendar. Pin one with:

```powershell
scar config set calendar_provider local   # google | outlook | local | auto
scar config set timezone Europe/Berlin    # optional; default = Windows time zone
```

Every tool also accepts `provider` per call.

## Google Calendar setup

Follow [gmail.md](gmail.md) steps 1-3 (enable **Google Calendar API**; scope
`https://www.googleapis.com/auth/calendar.events`). Mind the 7-day refresh-token expiry
while the consent screen is in *Testing* status - publish the app to avoid it.

## Outlook calendar setup

Follow [outlook.md](outlook.md) (scope `Calendars.ReadWrite`).

## Risk levels

* Creating an event on your own calendar: **MEDIUM** (auto-allowed at autonomy 3).
* Creating/updating an event **with attendees on Google/Outlook**: **HIGH** - the
  provider emails invitations, so SCAR asks first and shows every attendee. The local
  calendar never sends invitations.
* `calendar.update` and `calendar.delete`: **HIGH** (attendees may be notified).
* ICS import/export: **MEDIUM**; paths go through the path guard (allowed roots only,
  secret locations denied, overwriting an existing file is HIGH).

Times are ISO 8601 (`2026-10-05T09:00:00`); naive times use your time zone. The agent
converts phrases like "next Monday at 9" with `dateparser` before calling the tools.

## ICS support

SCAR writes RFC 5545 files itself: CRLF line endings, 75-octet line folding, TEXT
escaping, UTC `DTSTART/DTEND` (all-day events as `VALUE=DATE`), `SUMMARY`, `LOCATION`,
`DESCRIPTION`, `ATTENDEE;CN=...:mailto:...`, `UID` and a `VALARM` with
`TRIGGER:-PT<n>M` for reminders.

The importer understands `TZID=` (IANA names, common Windows names written by Outlook,
and Mozilla-style `/mozilla.org/.../Europe/Berlin`), `Z` UTC times, floating times,
`VALUE=DATE`, `DURATION`, and alarm triggers relative to start or end
(`RELATED=END`) or absolute (`VALUE=DATE-TIME`). Unknown time zones are read as local
time and reported as a warning. Importing into the local calendar skips events whose
`UID` already exists. Recurrence rules (`RRULE`) are not expanded: a recurring event is
imported as its first occurrence.
