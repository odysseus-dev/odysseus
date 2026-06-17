import { memo } from "react"
import ReactMarkdown from "react-markdown"
import remarkGfm from "remark-gfm"
import remarkMath from "remark-math"
import rehypeHighlight from "rehype-highlight"
import rehypeKatex from "rehype-katex"
import { ErrorBoundary } from "@/components/ErrorBoundary"

export const Markdown = memo(function Markdown({ children }: { children: string }) {
  return (
    <div className="prose-chat">
      {/* keyed by length so a transient malformed-LaTeX state during streaming
          recovers as more tokens arrive, instead of sticking on the fallback. */}
      <ErrorBoundary key={children.length} fallback={<pre className="whitespace-pre-wrap break-words text-sm">{children}</pre>}>
        <ReactMarkdown
          remarkPlugins={[remarkGfm, remarkMath]}
          rehypePlugins={[rehypeHighlight, rehypeKatex]}
        >
          {children}
        </ReactMarkdown>
      </ErrorBoundary>
    </div>
  )
})
