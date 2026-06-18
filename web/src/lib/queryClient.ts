import { QueryClient, MutationCache } from "@tanstack/react-query"
import { toast } from "@/stores/toast"

// Global safety net: any mutation that throws (e.g. an API call that checks
// res.ok) surfaces a toast, so failures are never silent. A mutation can opt
// out with meta: { silent: true } when it renders its own inline error.
export const queryClient = new QueryClient({
  mutationCache: new MutationCache({
    onError: (err, _vars, _ctx, mutation) => {
      if (mutation.meta?.silent) return
      toast(err instanceof Error && err.message ? err.message : "Something went wrong. Please try again.")
    },
  }),
  defaultOptions: { queries: { retry: 1, refetchOnWindowFocus: false, staleTime: 30_000 } },
})
