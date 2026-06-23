import { useEffect } from "react"
import { useQueryClient } from "@tanstack/react-query"
import { fetchEmailUnreadState, fetchLiveEmailUnreadCount } from "@/api/email"
import { toast } from "@/stores/toast"

// Mirrors the legacy emailInbox.js unread-count poll: every 60s ask the backend
// for the current unread total and raise a notification when it climbs. Query
// the live inbox as well as cached urgency so a delayed urgency worker cannot
// hide newly arrived mail.
const POLL_MS = 60_000
const NOTIFICATION_ICON = "/static/favicon.ico"
// Persist the last-seen unread count so a page reload doesn't re-announce mail
// the user already knows about.
const LAST_UNREAD_KEY = "odys.email.lastUnreadCount"

function loadLastUnread(): number | null {
  if (typeof localStorage === "undefined") return null
  const raw = localStorage.getItem(LAST_UNREAD_KEY)
  if (raw === null) return null
  const n = Number(raw)
  return Number.isFinite(n) ? n : null
}

function saveLastUnread(count: number): void {
  if (typeof localStorage === "undefined") return
  try {
    localStorage.setItem(LAST_UNREAD_KEY, String(count))
  } catch {
    /* ignore */
  }
}

function notifyNewMail(newCount: number, unreadTotal: number): void {
  const title = newCount === 1 ? "New email" : `${newCount} new emails`
  const body = unreadTotal === 1 ? "1 unread message" : `${unreadTotal} unread messages`
  if (typeof window !== "undefined" && "Notification" in window && Notification.permission === "granted") {
    try {
      const notification = new Notification(title, { body, tag: "email-new-mail", icon: NOTIFICATION_ICON })
      notification.onclick = () => {
        window.focus()
        window.location.assign("/v2/email")
        notification.close()
      }
      return
    } catch {
      /* fall through to toast */
    }
  }
  toast(`${title} — ${body}`, "info", 7000)
}

export function InboxPoller() {
  const queryClient = useQueryClient()

  useEffect(() => {
    let cancelled = false

    const poll = async () => {
      const [liveUnread, urgency] = await Promise.all([fetchLiveEmailUnreadCount(), fetchEmailUnreadState()])
      if (cancelled) return
      const unread = liveUnread ?? urgency?.total_unread
      if (unread == null) return
      const last = loadLastUnread()
      // First run (or cleared storage): record the baseline silently.
      if (last !== null && unread > last) {
        notifyNewMail(unread - last, unread)
        // New mail lands in INBOX — refresh only INBOX lists (any account/filter)
        // rather than every folder's cache. Key shape: ["email", folder, ...].
        void queryClient.invalidateQueries({ queryKey: ["email", "INBOX"] })
      }
      if (last === null || unread !== last) saveLastUnread(unread)
    }

    void poll()
    const timer = window.setInterval(() => { void poll() }, POLL_MS)
    return () => {
      cancelled = true
      window.clearInterval(timer)
    }
  }, [queryClient])

  return null
}
