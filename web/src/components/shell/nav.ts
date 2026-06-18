import {
  MessageSquare, GitCompareArrows, Image, Brain, Telescope,
  Calendar, Mail, StickyNote, ListChecks, FileText, FolderOpen, Database, FlaskConical, Sparkles,
} from "lucide-react"
import type { LucideIcon } from "lucide-react"

// Sidebar navigation entries. Shared by the Sidebar (renders them, honoring the
// per-user `hidden_nav` pref) and Settings → "Sidebar items" (show/hide toggles).
export interface NavItem { to: string; icon: LucideIcon; label: string }

export const PRIMARY: NavItem[] = [
  { to: "/chat", icon: MessageSquare, label: "Chat" },
  { to: "/compare", icon: GitCompareArrows, label: "Compare" },
  { to: "/research", icon: Telescope, label: "Research" },
  { to: "/gallery", icon: Image, label: "Gallery" },
  { to: "/memory", icon: Brain, label: "Memory" },
]
export const WORKSPACE: NavItem[] = [
  { to: "/calendar", icon: Calendar, label: "Calendar" },
  { to: "/email", icon: Mail, label: "Email" },
  { to: "/notes", icon: StickyNote, label: "Notes" },
  { to: "/tasks", icon: ListChecks, label: "Tasks" },
  { to: "/library", icon: FileText, label: "Library" },
  { to: "/personal", icon: FolderOpen, label: "Personal files" },
  { to: "/knowledge", icon: Database, label: "Knowledge" },
  { to: "/cookbook", icon: FlaskConical, label: "Cookbook" },
  { to: "/skills", icon: Sparkles, label: "Skills" },
]
