import { useEffect } from "react"
import { BrowserRouter, Routes, Route, Navigate, useLocation } from "react-router-dom"
import { QueryClientProvider } from "@tanstack/react-query"
import { queryClient } from "@/lib/queryClient"
import { AppShell } from "@/components/shell/AppShell"
import { ErrorBoundary } from "@/components/ErrorBoundary"
import { Toaster } from "@/components/ui/Toaster"
import { OnboardingDialog } from "@/components/onboarding/OnboardingDialog"
import { ChatConsole } from "@/routes/ChatConsole"
import { MemoryRoute } from "@/routes/MemoryRoute"
import { NotesRoute } from "@/routes/NotesRoute"
import { TasksRoute } from "@/routes/TasksRoute"
import { SettingsRoute } from "@/routes/SettingsRoute"
import { SkillsRoute } from "@/routes/SkillsRoute"
import { CalendarRoute } from "@/routes/CalendarRoute"
import { GalleryRoute } from "@/routes/GalleryRoute"
import { EmailRoute } from "@/routes/EmailRoute"
import { DocumentsRoute } from "@/routes/DocumentsRoute"
import { CompareRoute } from "@/routes/CompareRoute"
import { CookbookRoute } from "@/routes/CookbookRoute"
import { ResearchRoute } from "@/routes/ResearchRoute"
import { ProjectsRoute } from "@/routes/ProjectsRoute"
import { RagRoute } from "@/routes/RagRoute"
import { PersonalRoute } from "@/routes/PersonalRoute"
import { useUi } from "@/stores/ui"

const FONT_STACKS: Record<string, string> = {
  sans: "",
  serif: 'Georgia, "Times New Roman", serif',
  mono: 'ui-monospace, SFMono-Regular, "SF Mono", Menlo, Consolas, monospace',
}
const DENSITY_PX: Record<string, string> = { compact: "14px", comfortable: "16px", spacious: "17px" }

function ThemedApp() {
  const theme = useUi((s) => s.theme)
  const accent = useUi((s) => s.accent)
  const font = useUi((s) => s.font)
  const density = useUi((s) => s.density)
  const { pathname } = useLocation()
  // Key the error boundary by the top-level route segment, NOT the full path.
  // `/chat/:sessionId?` matches both `/chat` and `/chat/{id}`, so React Router
  // keeps ChatConsole mounted across a new-chat navigation — but keying by the
  // full pathname would change the key and force-remount it, orphaning the
  // in-flight stream (reply only reappears on manual refresh). Switching to a
  // different feature still changes the segment and resets the boundary.
  const routeKey = pathname.split("/")[1] || "home"
  useEffect(() => { document.documentElement.classList.toggle("dark", theme === "dark") }, [theme])
  useEffect(() => {
    const root = document.documentElement
    if (accent) { root.style.setProperty("--primary", accent); root.style.setProperty("--ring", accent); root.style.setProperty("--primary-foreground", "#ffffff"); root.style.setProperty("--sidebar-primary", accent) }
    else { for (const v of ["--primary", "--ring", "--primary-foreground", "--sidebar-primary"]) root.style.removeProperty(v) }
    root.style.fontFamily = FONT_STACKS[font] || ""
    root.style.fontSize = DENSITY_PX[density] || "16px"
  }, [accent, font, density])
  return (
    <AppShell>
      <ErrorBoundary key={routeKey}>
        <Routes>
          <Route path="/" element={<Navigate to="/chat" replace />} />
          <Route path="/chat/:sessionId?" element={<ChatConsole />} />
          <Route path="/projects" element={<ProjectsRoute />} />
          <Route path="/compare" element={<CompareRoute />} />
          <Route path="/research" element={<ResearchRoute />} />
          <Route path="/memory" element={<MemoryRoute />} />
          <Route path="/gallery" element={<GalleryRoute />} />
          <Route path="/calendar" element={<CalendarRoute />} />
          <Route path="/email" element={<EmailRoute />} />
          <Route path="/library" element={<DocumentsRoute />} />
          <Route path="/personal" element={<PersonalRoute />} />
          <Route path="/knowledge" element={<RagRoute />} />
          <Route path="/notes" element={<NotesRoute />} />
          <Route path="/tasks" element={<TasksRoute />} />
          <Route path="/cookbook" element={<CookbookRoute />} />
          <Route path="/skills" element={<SkillsRoute />} />
          <Route path="/settings" element={<SettingsRoute />} />
          <Route path="*" element={<Navigate to="/chat" replace />} />
        </Routes>
      </ErrorBoundary>
      <OnboardingDialog />
    </AppShell>
  )
}

export default function App() {
  return (
    <QueryClientProvider client={queryClient}>
      <BrowserRouter basename="/v2">
        <ThemedApp />
      </BrowserRouter>
      <Toaster />
    </QueryClientProvider>
  )
}
