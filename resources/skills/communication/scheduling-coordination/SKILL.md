---
name: scheduling-coordination
description: "Coordinate availability, confirmations, calendar changes, and participant notifications with read-back verification"
version: 1.0.0
category: communication
tags: [calendar, scheduling, coordination, availability]
status: published
confidence: 1.0
source: builtin
created: "2026-08-30T00:00:00Z"
---

## When to Use

Use when arranging or changing a meeting across multiple participants, calendars, time zones, or communication channels.

Do not create or modify an event when the user asked only for available options or a draft invitation.

## Procedure

1. Extract participants, duration, date range, time zones, location constraints, and required attendees.
2. Resolve participant identities and inspect the relevant availability using declared calendar and contact capabilities.
3. Compute candidate intervals in one explicit reference time zone and reject conflicts or insufficient travel buffers.
4. Present or draft a small set of viable options when confirmation is still required.
5. After authorization or recorded participant confirmation, create or update the event once with stable attendee identifiers.
6. Read the event back and verify title, start, end, time zone, attendees, location, and conferencing details before drafting notifications.

## Pitfalls

- Do not overwrite or cancel unrelated events to manufacture availability.
- Do not mix local times without naming the time zone.
- Do not treat a proposed time as confirmed.
- Do not create duplicates when an existing event can be updated safely.

## Verification

- The selected interval satisfies duration, availability, and time-zone constraints.
- The calendar read-back matches the authorized event details.
- Notifications describe the same confirmed event and remain drafts unless sending was explicitly authorized.
