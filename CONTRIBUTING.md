# Contributing to Odysseus

First of all: thank you. This project exploded in popularity very fast and is currently maintained primarily by one person (with a lot of AI assistance during development).

## Current Reality Check

- The project is **very new** (launched publicly in May 2026).
- Most incoming issues right now are **fresh install / platform friction** (especially Windows and macOS Docker issues).
- The maintainer has limited bandwidth. Please be patient and kind.
- Big architectural changes or massive new features are unlikely to be reviewed quickly unless you are also willing to maintain them.

## How You Can Actually Help Right Now

### High value (reduces maintainer load)
- **Triage and label issues** (many are duplicates or support requests)
- **Improve installation docs** and troubleshooting guides
- **Review and test pull requests** (especially Windows fixes and Docker improvements)
- **Write clear bug reports** with reproduction steps
- **Help other users** in issues instead of just adding "+1"

### Good but lower priority
- Small, focused refactors
- Mobile / editor polish
- Better error messages and empty states
- Provider integration fixes

See [ROADMAP.md](ROADMAP.md) for the current help-wanted areas.

## What Is Unlikely to Be Merged Quickly
- Large new features without prior discussion
- Major refactors of core systems (agent loop, tool system, app.py, etc.) without buy-in
- Changes that significantly increase complexity or attack surface

## Development Notes

- The architecture is... ambitious for the size of the team. See the discussion in the issue tracker if you're considering big changes.
- There is **no CI yet** (as of launch). Please test locally, especially on the platforms you claim to support.
- Security matters. The agent has real capabilities (shell, Python, email, calendar, file system). Changes that affect auth, tool permissions, or data ownership need extra care.

## Communication

If you're working on something non-trivial, please open an issue first to discuss. This prevents wasted effort on both sides.

Be excellent to each other. This project has a very "figure it out and ship" culture, but that only works when people are respectful.

---

**TL;DR:** Right now the highest leverage contributions are making the thing not suck on first boot for normal humans, not building new empires on top of it.
