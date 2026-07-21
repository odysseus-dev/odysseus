# Build App — ATLAS Workflow

## Goal

Build full-stack applications using AI assistance within the GOTCHA framework. This workflow ensures apps are production-ready, not just demos.

**ATLAS** is a 5-step process:

| Step | Phase | What You Do |
|------|-------|-------------|
| **A** | Architect | Define problem, users, success metrics |
| **T** | Trace | Data schema, integrations map, stack proposal |
| **L** | Link | Validate ALL connections before building |
| **A** | Assemble | Build with layered architecture |
| **S** | Stress-test | Test functionality, error handling |

## For prod builds when asked specifically add:
+ V - Validate (security/input sanitization, edge cases, unit tests)
+ M - Monitor (logging, observability, alerts)

---

## A — Architect

**Purpose:** Know exactly what you're building before touching code.

### Questions to Answer

1. **What problem does this solve?**
   - One sentence. If you can't say it simply, you don't understand it.

2. **Who is this for?**
   - Specific user: "Me" / "Sales team" / "YouTube subscribers"
   - Not "everyone"

3. **What does success look like?**
   - Measurable outcome: "I can see my metrics in one dashboard"
   - Not vague: "It works"

4. **What are the constraints?**
   - Budget (API costs)
   - Time (MVP vs full build)
   - Technical (must use Supabase, must integrate with X)

### Output

```markdown
## App Brief
- **Problem:** [One sentence]
- **User:** [Who specifically]
- **Success:** [Measurable outcome]
- **Constraints:** [List]
```

---

## T — Trace

**Purpose:** Design before building. This is where most "vibe coders" fail.

### Data Schema

Define your source of truth BEFORE building:

```
Tables:
- users (id, email, name, created_at)
- saved_items (id, user_id, title, content, source, created_at)
- metrics (id, user_id, platform, value, date)

Relationships:
- users 1:N saved_items
- users 1:N metrics
```

### Integrations Map

List every external connection:

| Service | Purpose | Auth Type | MCP Available? |
|---------|---------|-----------|----------------|
| Supabase | Database | API Key | Yes |
| YouTube API | Metrics | OAuth | Via MCP |
| Notion | Save items | API Key | Yes |

### Technology Stack Proposal

Based on requirements, propose:
- Database (Supabase, Firebase, Postgres, etc.)
- Backend (Supabase Functions, n8n, custom API)
- Frontend (React, Next.js, vanilla, etc.)
- Any other services needed

User approves or overrides before proceeding.

### Edge Cases

Document what could break:

- API rate limits (YouTube: 10,000 quota/day)
- Auth token expiry
- Database connection timeout
- Invalid user input
- MCP server unavailability

### Output

- Data schema diagram or markdown table
- Technology stack (approved by user)
- Integrations checklist
- Edge cases documented

---

## L — Link

**Purpose:** Validate all connections BEFORE building. Nothing worse than building for 2 hours then discovering the API doesn't work.

### Connection Validation Checklist

```
[ ] Database connection tested
[ ] All API keys verified
[ ] MCP servers responding
[ ] OAuth flows working
[ ] Environment variables set
[ ] Rate limits understood
```

### How to Test

**Database:**
```bash
# Test via MCP or direct API call
# Should return empty array or existing data, not error
```

**APIs:**
```bash
# Make a simple GET request
# Verify response format matches expectations
```

**MCPs:**
```
# List available tools
# Test one simple operation
```

### Output

All green checkmarks. If anything fails, fix it before proceeding.

---

## A — Assemble

**Purpose:** Build the actual application with proper architecture.

### Architecture Layers

Follow GOTCHA separation:

1. **Frontend** (what user sees)
   - UI components
   - User interactions
   - Display logic

2. **Backend** (what makes it work)
   - API routes
   - Business logic
   - Data validation

3. **Database** (source of truth)
   - Schema implementation
   - Migrations
   - Indexes

### Build Order

1. Database schema first
2. Backend API routes second
3. Frontend UI last

This order prevents building UI for data structures that don't exist.

### Component Strategy

- Use existing component libraries (don't reinvent buttons)
- Keep components small and focused
- Document any non-obvious logic

### Output

Working application with:
- Functional database
- API endpoints responding
- UI rendering correctly

---

## S — Stress-test

**Purpose:** Test before shipping. This is the step most "vibe coding" tutorials skip entirely.

### Functional Testing

Does it actually work?

```
[ ] All buttons do what they should
[ ] Data saves to database
[ ] Data retrieves correctly
[ ] Navigation works
[ ] Error states handled
```

### Integration Testing

Do the connections hold?

```
[ ] API calls succeed
[ ] MCP operations work
[ ] Auth persists across sessions
[ ] Rate limits not exceeded
```

### Edge Case Testing

What breaks?

```
[ ] Invalid input handled gracefully
[ ] Empty states display correctly
[ ] Network errors show feedback
[ ] Long text doesn't break layout
```

### User Acceptance

Is this what was wanted?

```
[ ] Solves the original problem
[ ] User can accomplish their goal
[ ] No major friction points
```

### Output

Test report with:
- What passed
- What failed
- What needs fixing

---

## Note: Deployment

Deployment is **not part of this workflow**. It's a separate, user-initiated action.

When you're ready to deploy, explicitly ask. This keeps deployment decisions in your control, not automated.

---

## Anti-Patterns (What NOT to Do)

These are the mistakes "vibe coders" make:

1. **Building before designing** — You end up rewriting everything
2. **Skipping connection validation** — Hours wasted on broken integrations
3. **No data modeling** — Schema changes cascade into UI rewrites
4. **No testing** — Ship broken code, lose trust
5. **Hardcoding everything** — No flexibility for changes

---

## GOTCHA Layer Mapping

| ATLAS Step | GOTCHA Layer |
|------------|--------------|
| Architect | Goals (define the process) |
| Trace | Context (reference patterns) |
| Link | Args (environment setup) |
| Assemble | Tools (execution) |
| Stress-test | Orchestration (AI validates) |


---

## Related Files

- **Args:** `args/app_defaults.yaml` (if created)
- **Context:** `context/ui_patterns/` (design references)
- **Hard Prompts:** `hardprompts/app_building/` (generation templates)

---

## Changelog