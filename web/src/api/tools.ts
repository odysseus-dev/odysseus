import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { apiFetch, apiJson } from '@/lib/api'

export interface BuiltinToolState { id: string; enabled: boolean }

export function useBuiltinTools() {
  return useQuery({
    queryKey: ['builtin-tools'],
    queryFn: async () => (await apiJson<{ tools?: BuiltinToolState[] }>('/api/tools')).tools || [],
  })
}

export function useSetBuiltinTools() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: async (tools: BuiltinToolState[]) => {
      const disabled = tools.filter((tool) => !tool.enabled).map((tool) => tool.id)
      const response = await apiFetch('/api/tools', {
        method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ disabled }),
      })
      if (!response.ok) throw new Error(response.status === 403 ? 'Only an administrator can change built-in tools.' : 'Could not update built-in tools.')
      return tools
    },
    onSuccess: (tools) => qc.setQueryData(['builtin-tools'], tools),
  })
}
