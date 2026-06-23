import { useEffect } from "react"
import { apiFetch } from "@/lib/api"
import { toast } from "@/stores/toast"

interface TaskNotification {
  task_name?: string
  status?: string
  task_id?: string
  body?: string
}

interface TaskNotificationsResponse {
  notifications?: TaskNotification[]
}

const POLL_MS = 30_000
const NOTIFICATION_ICON = "/static/favicon.ico"

function fireBrowserNotification(title: string, body: string, tag: string): boolean {
  if (!("Notification" in window) || Notification.permission !== "granted") return false
  try {
    new Notification(title, { body, tag, icon: NOTIFICATION_ICON })
    return true
  } catch {
    return false
  }
}

async function pollTaskNotifications(): Promise<void> {
  try {
    const res = await apiFetch("/api/tasks/notifications")
    if (!res.ok) return
    const data = await res.json().catch(() => ({})) as TaskNotificationsResponse
    for (const note of data.notifications || []) {
      const ok = note.status === "success"
      const name = note.task_name || "Task"
      const body = typeof note.body === "string" ? note.body : ""
      if (ok && body) {
        const fired = fireBrowserNotification(name, body, `task-${note.task_id || name}`)
        if (!fired) toast(`${name}: ${body.slice(0, 140)}`, "success", 7000)
        continue
      }
      toast(`Task ${ok ? "finished" : "failed"}: ${name}`, ok ? "success" : "error")
    }
  } catch {
    // Background notification polling should never interrupt the app shell.
  }
}

export function TaskNotificationPoller() {
  useEffect(() => {
    void pollTaskNotifications()
    const timer = window.setInterval(() => { void pollTaskNotifications() }, POLL_MS)
    return () => window.clearInterval(timer)
  }, [])

  return null
}
