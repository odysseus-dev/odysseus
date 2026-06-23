import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { fireEvent, render, screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import { GalleryRoute } from './GalleryRoute'

const { image, mutation } = vi.hoisted(() => ({
  image: { id: 'img-1', filename: 'sample.png', url: '/api/gallery/img-1', prompt: 'Sample' },
  mutation: { mutate: vi.fn(), isPending: false },
}))

vi.mock('@/api/gallery', () => ({
  useGallery: () => ({ data: {}, isFetching: false, fetchNextPage: vi.fn(), hasNextPage: false, isFetchingNextPage: false }),
  flattenGallery: () => ({ items: [image], total: 1, total_tagged: 0, models: [] }),
  useGalleryAlbums: () => ({ data: [] }),
  useGalleryMutations: () => ({ favorite: mutation, remove: mutation, upload: mutation, createAlbum: mutation, renameAlbum: mutation, deleteAlbum: mutation, setAlbumCover: mutation, aiTagAll: mutation }),
  downloadImage: vi.fn(),
}))
vi.mock('@/lib/api', () => ({ apiJson: vi.fn(async () => ({ drafts: [] })), apiFetch: vi.fn(async () => new Response('{}', { status: 200 })) }))

describe('Gallery native cutover', () => {
  it('keeps editor and settings inside v2 and launches the isolated editor frame', async () => {
    render(<QueryClientProvider client={new QueryClient()}><GalleryRoute /></QueryClientProvider>)
    fireEvent.click(screen.getByRole('button', { name: /editor/i }))
    expect(await screen.findByText('New canvas')).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: /sample/i }))
    const frame = await screen.findByTitle('Gallery image editor')
    expect(frame).toHaveAttribute('src', expect.stringContaining('/v2/gallery-editor-frame'))
    expect(frame.getAttribute('src')).not.toContain('odysseus_open')
    fireEvent.click(screen.getByRole('button', { name: /projects & photos/i }))
    fireEvent.click(screen.getByRole('button', { name: /settings/i }))
    expect(await screen.findByText('AI tagging')).toBeInTheDocument()
    expect(screen.queryByText(/open original/i)).not.toBeInTheDocument()
  })
})
