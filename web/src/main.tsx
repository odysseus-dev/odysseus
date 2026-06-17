import { StrictMode } from "react"
import { createRoot } from "react-dom/client"
import "./index.css"
import "katex/dist/katex.min.css"
import "highlight.js/styles/github-dark.min.css"
import App from "./App"

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <App />
  </StrictMode>,
)
