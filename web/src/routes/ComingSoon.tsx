export function ComingSoon({ title }: { title: string }) {
  return (
    <div className="flex h-full flex-col">
      <header className="flex h-13 shrink-0 items-center border-b px-4 text-sm font-semibold">{title}</header>
      <div className="flex flex-1 items-center justify-center p-8 text-center">
        <div>
          <h1 className="text-xl font-semibold tracking-tight">{title}</h1>
          <p className="mt-2 text-sm text-muted-foreground">Being ported to the v2 console — coming soon.</p>
        </div>
      </div>
    </div>
  )
}
