# LID Planning: Task Scheduler Defaults and Visibility

## Landscape
Odysseus relies on background tasks and schedulers for features like email triage, calendar syncing, note reminders, and deep research jobs. Currently, the task scheduler's default intervals and retry behaviors may not be perfectly tuned, potentially leading to excessive polling or delayed executions. Furthermore, there is a lack of visibility into the scheduler's state. Administrators cannot easily see what tasks are currently running, when the next tasks are scheduled, or view logs of recent task failures. This opacity makes debugging scheduled tasks difficult and reduces confidence in the system's reliability.

## Initiative
We need to refactor the background task scheduling system to introduce sensible, production-ready defaults and build a visibility layer for administrators. This involves auditing the current scheduled tasks, adjusting their frequencies, implementing exponential backoff for retries, and exposing the scheduler's internal state. The goal is to make the system more efficient and completely transparent to the admin user.

## Deliverable
- **Optimized Defaults**: Updated code in the scheduler core (`core/` or `services/`) with refined default polling intervals, timeout limits, and retry policies for all background jobs.
- **Visibility API / Endpoints**: New admin-gated REST or WebSocket endpoints (e.g., `GET /api/admin/tasks`) that return the current state of the scheduler, active jobs, queued jobs, and a history of recent executions/failures.
- **UI Integration**: (Optional but recommended) A basic Admin UI view to consume the new visibility endpoints, showing a dashboard of scheduled tasks.
- **Logging Improvements**: Enhanced structured logging for all scheduled tasks to ensure failures are easily traceable in the Docker logs or native console output.
