import { useEffect } from "react"
import { BrowserRouter, Routes, Route, Navigate } from "react-router-dom"
import { QueryClientProvider } from "@tanstack/react-query"
import { queryClient } from "@/lib/queryClient"
import { AppShell } from "@/components/shell/AppShell"
import { ChatConsole } from "@/routes/ChatConsole"
import { useUi } from "@/stores/ui"

function ThemedApp() {
  const theme = useUi((s) => s.theme)
  useEffect(() => {
    document.documentElement.classList.toggle("dark", theme === "dark")
  }, [theme])
  return (
    <AppShell>
      <Routes>
        <Route path="/" element={<Navigate to="/chat" replace />} />
        <Route path="/chat/:sessionId?" element={<ChatConsole />} />
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
