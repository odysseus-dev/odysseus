import logging
import uuid as _uuid

logger = logging.getLogger(__name__)


class ManageTasksTool:
    async def execute(self, content: str, ctx: dict) -> dict:
        owner = ctx.get("owner")
        from src.tool_implementations import _parse_tool_args
        from core.database import SessionLocal, ScheduledTask
        from src.task_scheduler import compute_next_run

        try:
            args = _parse_tool_args(content)
        except ValueError:
            return {"error": "Invalid JSON arguments", "exit_code": 1}

        action = args.get("action", "list")
        db = SessionLocal()
        try:
            if action == "list":
                q = db.query(ScheduledTask)
                if owner:
                    q = q.filter(ScheduledTask.owner == owner)
                tasks = q.order_by(ScheduledTask.created_at.desc()).all()
                task_list = []
                for t in tasks:
                    task_list.append({
                        "id": t.id, "name": t.name, "status": t.status,
                        "task_type": t.task_type or "llm",
                        "action": t.action,
                        "trigger_type": t.trigger_type or "schedule",
                        "schedule": t.schedule,
                        "trigger_event": t.trigger_event,
                        "trigger_count": t.trigger_count,
                        "next_run": t.next_run.isoformat() + "Z" if t.next_run else None,
                        "last_run": t.last_run.isoformat() + "Z" if t.last_run else None,
                        "run_count": t.run_count or 0,
                    })
                return {"response": f"Found {len(task_list)} tasks", "tasks": task_list, "exit_code": 0}

            elif action == "create":
                task_type = args.get("task_type", "llm")
                trigger_type = args.get("trigger_type", "schedule")

                if task_type in ("llm", "research") and not args.get("prompt"):
                    return {"error": "Prompt is required for llm/research tasks", "exit_code": 1}
                if task_type == "action" and not args.get("action_name"):
                    return {"error": "action_name is required for action tasks", "exit_code": 1}

                next_run = None
                if trigger_type == "schedule":
                    schedule = args.get("schedule", "daily")
                    next_run = compute_next_run(
                        schedule, args.get("scheduled_time", "09:00"),
                        args.get("scheduled_day"),
                    )

                task_id = str(_uuid.uuid4())
                name = args.get("name") or (args.get("prompt") or args.get("action_name") or "Task")[:50]

                task = ScheduledTask(
                    id=task_id,
                    owner=owner,
                    name=name,
                    prompt=args.get("prompt"),
                    task_type=task_type,
                    action=args.get("action_name"),
                    schedule=args.get("schedule") if trigger_type == "schedule" else None,
                    scheduled_time=args.get("scheduled_time", "09:00") if trigger_type == "schedule" else None,
                    scheduled_day=args.get("scheduled_day"),
                    trigger_type=trigger_type,
                    trigger_event=args.get("trigger_event"),
                    trigger_count=args.get("trigger_count"),
                    trigger_counter=0,
                    next_run=next_run,
                    status="active",
                    output_target=args.get("output_target", "session"),
                )
                db.add(task)
                db.commit()
                return {"response": f"Created task '{name}' (id: {task_id})", "task_id": task_id, "exit_code": 0}

            elif action == "edit":
                task_id = args.get("task_id")
                if not task_id:
                    return {"error": "task_id is required for edit", "exit_code": 1}
                task = db.query(ScheduledTask).filter(ScheduledTask.id == task_id).first()
                if not task:
                    return {"error": f"Task {task_id} not found", "exit_code": 1}
                if owner and task.owner and task.owner != owner:
                    return {"error": "Access denied", "exit_code": 1}

                changed = []
                for field in ("name", "prompt", "output_target"):
                    if args.get(field) is not None:
                        setattr(task, field, args[field])
                        changed.append(field)
                if args.get("task_type") is not None:
                    task.task_type = args["task_type"]
                    changed.append("task_type")
                if args.get("action_name") is not None:
                    task.action = args["action_name"]
                    changed.append("action")
                if args.get("trigger_type") is not None:
                    task.trigger_type = args["trigger_type"]
                    changed.append("trigger_type")
                if args.get("trigger_event") is not None:
                    task.trigger_event = args["trigger_event"]
                    changed.append("trigger_event")
                if args.get("trigger_count") is not None:
                    task.trigger_count = args["trigger_count"]
                    changed.append("trigger_count")

                schedule_changed = False
                for field in ("schedule", "scheduled_time", "scheduled_day"):
                    if args.get(field) is not None:
                        setattr(task, field, args[field])
                        changed.append(field)
                        schedule_changed = True

                if schedule_changed and (task.trigger_type or "schedule") == "schedule":
                    task.next_run = compute_next_run(
                        task.schedule, task.scheduled_time, task.scheduled_day,
                    )

                db.commit()
                return {"response": f"Updated task '{task.name}': {', '.join(changed)}", "exit_code": 0}

            elif action == "delete":
                task_id = args.get("task_id")
                if not task_id:
                    return {"error": "task_id is required for delete", "exit_code": 1}
                task = db.query(ScheduledTask).filter(ScheduledTask.id == task_id).first()
                if not task:
                    return {"error": f"Task {task_id} not found", "exit_code": 1}
                if owner and task.owner and task.owner != owner:
                    return {"error": "Access denied", "exit_code": 1}
                name = task.name
                db.delete(task)
                db.commit()
                return {"response": f"Deleted task '{name}'", "exit_code": 0}

            elif action in ("pause", "resume"):
                task_id = args.get("task_id")
                if not task_id:
                    return {"error": f"task_id is required for {action}", "exit_code": 1}
                task = db.query(ScheduledTask).filter(ScheduledTask.id == task_id).first()
                if not task:
                    return {"error": f"Task {task_id} not found", "exit_code": 1}
                if owner and task.owner and task.owner != owner:
                    return {"error": "Access denied", "exit_code": 1}

                if action == "pause":
                    task.status = "paused"
                else:
                    task.status = "active"
                    if (task.trigger_type or "schedule") == "schedule":
                        task.next_run = compute_next_run(
                            task.schedule, task.scheduled_time, task.scheduled_day,
                        )
                db.commit()
                return {"response": f"Task '{task.name}' {action}d", "exit_code": 0}

            elif action == "run":
                task_id = args.get("task_id")
                if not task_id:
                    return {"error": "task_id is required for run", "exit_code": 1}
                task = db.query(ScheduledTask).filter(ScheduledTask.id == task_id).first()
                if not task:
                    return {"error": f"Task {task_id} not found", "exit_code": 1}
                if owner and task.owner and task.owner != owner:
                    return {"error": "Access denied", "exit_code": 1}

                from src.event_bus import get_task_scheduler
                scheduler = get_task_scheduler()
                if scheduler:
                    started = await scheduler.run_task_now(task_id)
                    if started:
                        return {"response": f"Task '{task.name}' triggered", "exit_code": 0}
                    else:
                        return {"error": "Task is already running", "exit_code": 1}
                return {"error": "Task scheduler not available", "exit_code": 1}

            else:
                return {"error": f"Unknown action: {action}", "exit_code": 1}

        except Exception as e:
            logger.error(f"manage_tasks error: {e}")
            return {"error": str(e), "exit_code": 1}
        finally:
            db.close()
