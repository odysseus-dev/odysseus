import { render, screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import { CookbookRoute } from './CookbookRoute'

const { mutation } = vi.hoisted(() => ({ mutation: { mutate: vi.fn(), mutateAsync: vi.fn(), isPending: false } }))
vi.mock('@/api/cookbook', () => ({
  useCachedModels: () => ({ data: { models: [], host: 'local', ok: false }, isLoading: false }),
  useGpus: () => ({ data: { ok: false, gpus: [], error: 'forbidden' }, isLoading: false }),
  useCookbookMutations: () => ({ download: mutation, serve: mutation }),
  useRunningTasks: () => ({ data: { ok: false, tasks: [] }, isLoading: false, isError: false }),
  useRunningMutations: () => ({ stop: mutation, registerServeTask: vi.fn() }),
  useHwfitModels: () => ({ data: { system: { gpu_name: 'Test GPU', gpu_vram_gb: 24 }, models: [{ name: 'org/model', fit_level: 'good', required_gb: 12, speed_tps: 30 }] }, isLoading: false }),
  useHfLatest: () => ({ data: [], isLoading: false }), useOllamaLibrary: () => ({ data: [], isLoading: false }),
  useServeProfiles: () => ({ data: { profiles: [] } }), useImageFit: () => ({ data: { models: [] }, isLoading: false }),
  useRecipeManifest: () => ({ data: { models: [] } }), useVllmRecipe: () => ({ data: {}, isLoading: false }),
  useCookbookSetup: () => mutation,
  SERVE_BACKENDS: [{ value: 'vllm', label: 'vLLM' }], buildServeCmd: () => '',
}))

describe('Cookbook delegated discovery', () => {
  it('keeps public hardware-fit discovery useful when operational admin endpoints are forbidden', () => {
    render(<CookbookRoute />)
    expect(screen.getByText('org/model')).toBeInTheDocument()
    expect(screen.getByText('Test GPU')).toBeInTheDocument()
    expect(screen.getByText(/Running-task status unavailable/)).toBeInTheDocument()
    expect(screen.getByText('Provisioning & recipes')).toBeInTheDocument()
  })
})
