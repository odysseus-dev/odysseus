# Core blocks — blank-slate template

## The premise

This memory system is a **blank slate**. It ships with no facts about any
user, no inherited identity, no pre-loaded preferences. What it ships is the
*mechanism*: how memory should grow, be protected, and stay honest.

As the user interacts with the agent, the system **records facts**. Each
verified fact becomes an entry in the hybrid store; over time the store
accumulates what is genuinely true about the relationship between the user
and the agent — the user's preferences, projects, values, how they like to
work, what they care about. That accumulated record **informs the agent**:
recall surfaces it in context, the persona forms from it, and the agent
becomes more attuned to the person it works with — through use, not through
hardcoding.

The five core blocks are the **stable summary** of that relationship,
protected from drift:

| Block | What it holds | How it grows |
|---|---|---|
| `constitution` | Non-negotiable directives (truth, honesty, how you operate) | **Only by explicit directive** — the tightest bound |
| `persona` | Who the agent is: identity values, delivery register | Evidence-gated — forms only on real grounding, via the worthiness gate |
| `human` | Verified facts about the user | Grows from verified facts and sessions only |
| `operating` | How the agent behaves (rules of conduct) | Grows from confirmed directives and amended rules |
| `project` | What the user works on | Grows from verified project state |

The drift ledger records the sha256 + byte length of each block at anchor.
Each consolidation run compares against the anchor; any change must be
justified by a journaled operation (a directive, a promotion, a curator
ADD/UPDATE). Unexplained bulk is flagged as drift.

---

## The lifecycle: blank slate → interaction → growth

1. **Blank slate** — a fresh store. No `human`, `persona`, `project`, or
   `operating` values; an empty constitution (or only the platform's own
   method directives if you choose to seed them).

2. **Interaction records facts** — every session, verified facts are mined
   into the store: what the user said, did, preferred, decided. Each entry
   carries provenance (source, time, verification grade).

3. **The relationship accumulates** — recall surfaces the relevant facts in
   context; the curator promotes well-grounded, repeated patterns; the
   worthiness gate blocks what isn't grounded. Over time the store becomes a
   genuine record of *who this user is and how they work*.

4. **The agent is informed** — the accumulated record shapes persona,
   operating rules, and project state (evidence-gated), and recall injects
   the relevant facts into each turn. The agent's memory *is* the
   relationship, built from use.

---

## How to seed a fresh store

The blocks are stored as `always_on` entries in the hybrid store, keyed by
`topic`:

```bash
# constitution (read-only by directive)
MEMORY_STORE_DB=/path/to/store.db python3 memory_store.py \
  add --text "YOUR CONSTITUTION VALUE" --topic constitution --always-on

# persona / human / operating / project
MEMORY_STORE_DB=/path/to/store.db python3 memory_store.py \
  add --text "YOUR VALUE" --topic persona --always-on
```

Then snapshot the anchor:

```bash
python3 drift-ledger.py snapshot
```

From that point, the drift ledger protects what you seeded: legitimate growth
is journaled, unexplained change is flagged, and the constitution can only
change by explicit directive.

---

## What belongs in each block (structure only)

**constitution** — the inviolable rules you will not bend. Examples of the
*shape* (not values): a truthfulness rule, a source-revealing rule, a
harness/engine separation rule. Fill with your own.

**persona** — two sections:
- *Identity*: the values that define who the agent is (evidence-gated — only
  grounded, verified patterns belong here).
- *Delivery register*: the tone the agent presents in (flavour only, never a
  filter on content). Fill with your own; the worthiness gate keeps it
  grounded.

**human** — verified facts about the user. Starts blank; grows only from
confirmed sessions and verified material.

**operating** — the agent's rules of conduct (how to answer, when to be
direct, how to handle a conceded argument). Starts blank; grows from
confirmed directives and amended rules.

**project** — what the user works on. Starts blank; grows from verified
project state.

---

## Why they start blank

A memory that ships pre-filled with someone else's identity, preferences, or
values is not a blank slate — it is an inherited persona. This platform ships
the **method** (how memory should grow, be protected, and stay honest) but no
**content** about any particular user. Whoever installs it starts clean, and
the relationship record grows from their own interactions, their own facts,
their own evidence.
