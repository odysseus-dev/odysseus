# Design System — odysseus

## Product Context
- **What this is:** A self-hosted AI workspace for chat, agents, research, documents, email, notes, calendar, and local model workflows.
- **Who it's for:** Engineers, operators, and power users running their own stack.
- **Space/industry:** Self-hosted developer tools / AI infrastructure.
- **Project type:** Dashboard-heavy web app with chat and document surfaces.

## Aesthetic Direction
- **Direction:** Brutally Minimal + Industrial Utilitarian
- **Decoration level:** Minimal
- **Mood:** Calm, serious, functional. Interface recedes; content and data come forward.
- **Reference sites:** Linear-style density, Raycast restraint, terminal/host palette.

## Typography
- **Display/Hero:** Cabinet Grotesk — compact, contemporary, serious without shouting
- **Body:** Geist — optimized for interface density, easy on long sessions
- **UI/Labels:** same as body
- **Data/Tables:** Geist with tabular-nums
- **Code:** JetBrains Mono
- **Loading strategy:** CDN with font-display swap
- **Scale:** h1/20, title/16, body/14, small/12, tiny/11, mono/13

## Color
- **Approach:** Restrained dark-first
- **Base:** #020617
- **Surface:** #0f172a
- **Elevated:** #334155
- **Accent:** #38bdf8 — used sparingly as interactive signal only
- **Neutrals:** slate-50 through slate-900; avoid warm/purple bias
- **Semantic:** success #34d399, warning #fbbf24, error #f87171, info #38bdf8
- **Dark mode:** native dark default
- **Light mode:** inverted surfaces with reduced saturation and preserved contrast

## Spacing
- **Base unit:** 4px
- **Density:** comfortable, not airy
- **Scale:** 2xs(2) xs(4) sm(8) md(16) lg(24) xl(32) 2xl(48)

## Layout
- **Approach:** grid-disciplined
- **Grid:** 12 columns
- **Max content width:** 1280px
- **Border radius:** sm:4px, md:6px, lg:10px, full:9999px

## Motion
- **Approach:** minimal-functional
- **Easing:** enter ease-out, exit ease-in
- **Duration:** micro 50-80ms, short 150-200ms, medium 250-300ms

## Decisions Log
| Date | Decision | Rationale |
|------|----------|-----------|
| 2026-07-24 | Initial design system | Calm serious workspace for self-hosted AI infra |
