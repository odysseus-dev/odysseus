import { useEffect } from "react"
import { BrowserRouter, Routes, Route, Navigate } from "react-router-dom"
import { QueryClientProvider } from "@tanstack/react-query"
import { queryClient } from "@/lib/queryClient"
import { AppShell } from "@/components/shell/AppShell"
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
import { useUi } from "@/stores/ui"

function ThemedApp() {
  const theme = useUi((s) => s.theme)
  useEffect(() => { document.documentElement.classList.toggle("dark", theme === "dark") }, [theme])
  return (
    <AppShell>
      <Routes>
        <Route path="/" element={<Navigate to="/chat" replace />} />
        <Route path="/chat/:sessionId?" element={<ChatConsole />} />
        <Route path="/compare" element={<CompareRoute />} />
        <Route path="/memory" element={<MemoryRoute />} />
        <Route path="/gallery" element={<GalleryRoute />} />
        <Route path="/calendar" element={<CalendarRoute />} />
        <Route path="/email" element={<EmailRoute />} />
        <Route path="/library" element={<DocumentsRoute />} />
        <Route path="/notes" element={<NotesRoute />} />
        <Route path="/tasks" element={<TasksRoute />} />
        <Route path="/cookbook" element={<CookbookRoute />} />
        <Route path="/skills" element={<SkillsRoute />} />
        <Route path="/settings" element={<SettingsRoute />} />
        <Route path="*" element={<Navigate to="/chat" replace />} />
      </Routes>
    </AppShell>
  )
}

export default function App() {
  return (
    <QueryClientProvider client={queryClient}>
      <BrowserRouter basename="/v2">
        <ThemedApp />
      </BrowserRouter>
    </QueryClientProvider>
  )
}
