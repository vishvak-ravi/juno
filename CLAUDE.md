# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## CRITICAL: Explain concretely, from first principles

Every statement must name a real, pointable thing — a file, function, object, field, or value. A vague phrase used as an explanation (`share a net`, `the view`, `continuity`, `the pipeline`) is banned unless defined in the same breath in terms of actual code (not "the docs don't share a net" but "no `Coupling` object references both `TB-P.t1` and `TR_CTRL`"). When stating a limitation, trace the actual path step by step and mark each step checked/unchecked with the concrete reason. If a sentence would only make sense to someone who already knows what you mean, replace it with the concrete object.

## CRITICAL RULES TO REMEMBER

Note these down as critical rules that are to be followed in this repository at all times.

### CRITICAL: DO NOT CREATE DOCUMENTATION FILES

**NEVER create .md files unless the user EXPLICITLY requests them.** This includes:
- Architecture documents
- Implementation guides
- README files
- Migration plans
- Design documents
- Any other markdown documentation

**Instead**: Provide explanations, architecture recommendations, and code examples directly in chat responses. The user will ask you to create files if they want them saved.

### CRITICAL: No User-Specific Examples in Code

**When writing docstrings, prompts, comments, or code examples, NEVER copy the user's specific test cases, queries, or scenarios from the conversation.** Always use generic, illustrative examples — or omit examples entirely if the instruction is clear without them. This applies to error messages, variable names, and any other place where you might be tempted to parrot back what the user just said. Especially never in prompts.

### CRITICAL: Check Internal Docs First

**Before doing ANYTHING, check `internal-docs/` first.** There are existing guides for common tasks (clearing queues, deploying, managing data, etc.). Search internal docs before searching the codebase or proposing solutions. The answer is probably already written down.

See [internal-docs/misc-development-rules.md](internal-docs/misc-development-rules.md) for repo-wide rules that don't warrant their own doc (no base64 across Hyrex boundaries, no absolute paths in committed code, async work always goes through Hyrex, etc.).

### CRITICAL: Search the Codebase Before Doing Anything

**Before adding, installing, or building ANYTHING, search the codebase first.** Assume the thing you're about to create already exists until proven otherwise.

- **Before installing a tool/dependency**: Search for existing config files, package.json entries, CI workflows, and check `git show main:<path>` for files not on the current branch
- **Before creating a new file**: Search for existing files that already do the same thing
- **Before adding a new pattern**: Search for how the codebase already handles similar cases
- **If the user says "set up X"**: That probably means "enable the existing X", not "install a brand new X from scratch"

**The cost of searching is near zero. The cost of blindly adding redundant shit is massive.** When in doubt, ask the user.

**Always search for context of at least 100 lines** when using grep, read, or other search tools. This ensures you have sufficient surrounding context to understand the code properly.

**Before you write ANY utility function — grep for it first.** This is non-skippable, not optional. Utility functions feel small enough to skip the search but they're exactly where parallel implementations accumulate and drift from each other. JSON response parsing, model resolution, image encoding, retry logic, ID conversion, env loading — every one of these has already been written here. If you can't find it after a real grep, ask the user before writing it.

### CRITICAL: Understand Architecture Before Proposing Changes

**When modifying a feature, first understand the full system it operates in.**

1. **Understand the pipeline/architecture first**
   - What are the processing stages? (e.g., document → pages → components → formatting)
   - What modules/services are involved?
   - How does data flow through the system?

2. **Map where the feature intersects the pipeline**
   - At which stages could this feature be used?
   - What are the entry points and consumers?
   - Are there multiple independent usages?

3. **Verify with broad searches**
   - Search for the feature across the entire codebase to confirm understanding
   - Don't assume a config file or single location is exhaustive

**Bad:** Find `transcribe_text_with_vlm` in options.py → assume that's all VLM usage

**Good:** Understand document processing pipeline → identify VLM could be used in text, tables, flowcharts, descriptions → search to find all files with VLM references → trace actual usage patterns

### CRITICAL: When Given an Error or Traceback, Diagnose Root Cause First

**When the user pastes an error, traceback, or unexpected behavior:**

1. **Read the actual source code** at the locations referenced in the traceback
2. **Trace the full execution path** - don't guess based on function names alone
3. **Identify the root cause** before proposing any fix
4. **Never say "might" or "could be"** when you haven't read the code yet - go read it first

**DON'T**: See an error message and immediately propose a fix based on assumptions
**DO**: Read the relevant files, trace the data flow, then explain the root cause with evidence

### CRITICAL: Never Hallucinate Code - Always Read First

**NEVER fabricate class constructors, function signatures, or module structures.** If you haven't read a file, you don't know what's in it.

- Before using or modifying any class/function, READ the actual source file first
- Don't assume constructor parameters, method signatures, or class hierarchies
- Don't invent initialization patterns - check how existing code actually instantiates things
- If a function is a stub (e.g., in op-core/hyrex/tasks/), the real implementation is elsewhere (op-parseport) - go find it

### CRITICAL: No Inline Imports in Python

**NEVER use inline imports in Python code.** All imports MUST be at the top of
the file. Inline imports hide dependencies, cause circular-import bugs that
only surface at runtime, and can mask per-call import overhead.

### CRITICAL: Never hardcode a model name, threshold, or tunable knob

**If you find yourself writing a literal model name (e.g. `"gpt-4.1"`,
`"gemini-3.1-flash-lite"`), a default confidence threshold, or any other
tunable knob outside the existing config surfaces, STOP.** Add it to the
existing pattern instead.

In this repo:

- **Models**: extend `MODEL_ROLE_DEFINITIONS` in
  `op-core/src/op_core/config.py` with a new role (description + default).
  Wire to a Pydantic field via `_model_field`. At call time look it up via
  `resolve_model_for_user(api_settings, "<your_role>", repos, user_id)` —
  the same path every existing role uses. Chainsaw/OP tier users
  automatically get per-tier override via the Settings UI for free.
- **Per-call knobs** (thresholds, timeouts, feature toggles): expose on the
  Pydantic Context (Hyrex task) or request body with a default — never a
  literal in the function body.
- **Global toggles**: extend `ApiSettings` with a Pydantic field + env-var
  binding.

Hardcoded models / thresholds defeat the entire configuration system,
deny users the ability to swap models in response to pricing/quality
shifts, and turn every model bump into a code+deploy cycle.

When in doubt, grep for `MODEL_ROLE_DEFINITIONS` or look at how the
neighbouring code resolves its model — the pattern is well-developed
across this repo.

### CRITICAL: Diagnose before blaming external dependencies

**When an external call (LLM, API, downstream service) "returns nothing" /
"returns null" / "fails uniformly", verify your own code is the cause
before reporting it as a model or service problem.**

Specifically:

1. **Print the raw response** before parsing. Don't reason about what
   "null" means from inside your own error-catching code.
2. **Look for swallowed exceptions.** Broad `try/except: return None`
   in the call path will turn every cause — network error, malformed
   JSON, parse failure, legitimate null — into the same observable
   output. That makes "the model is broken" look identical to "my
   code has a bug".
3. **Look for input-side limits you imposed.** Reasoning models
   (gpt-5-series, gemini-3-series) consume output tokens for hidden
   thinking; a low `max_tokens` budget can yield an empty visible
   response that looks like a model failure but is actually budget
   starvation. Similarly: image-size limits, prompt truncation,
   content-policy filters, schema-validation failures upstream.
4. **Uniform failure across queries is a system signature, not an
   accuracy signature.** If every call for a given model returns the
   same nothing, the bug is almost certainly upstream of the model.

Concrete past mistake: I reported "gpt-5-nano: 0/8 accuracy,
gemini-3-flash-preview: 0/8 accuracy" as model-quality results. Both
were actually `max_tokens=200` starving the reasoning step + a broad
`except Exception: return None` in my matcher hiding the real cause.
Fixed by bumping max_tokens to 2000; both then scored 100%.

When you find yourself writing "model X seems broken" in a report,
*go print the raw response first*.

### CRITICAL: Never use `drizzle-kit push`

**NEVER run `drizzle-kit push`** (in any environment, but especially production). It bypasses migration history and directly syncs the schema, which leaves the `drizzle.__drizzle_migrations` table and the real schema out of sync — subsequent `drizzle-kit migrate` runs then fail or skip migrations silently. Always use `drizzle-kit generate` followed by `drizzle-kit migrate`.

### CRITICAL: Agent Tool Data Accessibility

When adding or modifying agent tools, every tool parameter must be answerable by the LLM from its context (system prompt, conversation history, or prior tool results). If a parameter requires a server-side identifier (UUID, internal ID, storage key, etc.) that the LLM has no way to discover, the tool WILL silently fail at runtime.

**Checklist for every tool parameter:**
1. Can the LLM determine this value from the conversation? (e.g., user typed it)
2. Is it surfaced in the system prompt or message metadata? (e.g., attachment UUIDs)
3. Can it be obtained from a prior tool call's output? (e.g., search → use result ID)
4. Is it a free-form value the LLM generates itself? (e.g., output_format="mermaid")

If none of these apply, the tool is broken — fix the data pipeline before shipping the tool.

**Common failure cases:**
- Image/file attachments: LLM receives image bytes but not the attachment UUID
- Document references: LLM knows the document name but not the internal ID
- Session/context IDs: Tool needs a session_id the LLM was never told about

**Where identifiers get surfaced:**
- Message converters (`openai_executor.py`, `anthropic_executor.py`, `google_executor.py`) — annotate messages with metadata the LLM will need for tools
- System prompt — include session-level context (session_id, available resources)
- Tool results — return IDs that downstream tools might need

### CRITICAL: Never Make Users Watch the Implementation Load

**Loading states must reflect user-meaningful progress, not implementation stages.** The user does not know — and must never be made to see — that a page is made of 4 API calls, N module loads, or a chain of async transitions. From the user's seat there is exactly one transition: `click → loading → data`. Anything finer is leaking your architecture into their eyes.

This is a recurring rot in this app (the knowledge-graph pages are the canonical offender): each intermediate fetch/hydration step renders its own distinct spinner, skeleton, or "loading X…" message, so the user watches the implementation assemble itself piece by piece. That is a bug, not a feature.

**Rules:**
1. **One logical loading state per user action.** Collapse all the intermediate async work (parallel API calls, dependent fetches, store hydration, lazy module loads) behind a *single* pending state. The user sees one spinner/skeleton, then the finished result — never a flicker through 2, 3, 4 partial states.
2. **No leaky async transitions.** Do not let each `await`, each request boundary, or each `useEffect` step paint its own intermediate UI. Resolve the whole dependency chain, then render once. Prefer `Promise.all` / a single server action / suspense boundary at the *feature* level over per-call boundaries.
3. **Don't name implementation details in user-facing copy.** No "Loading module…", "Fetching nodes…", "Building graph…", "Step 2 of 4". If you must show progress for a genuinely long operation, describe it in the user's terms (what they're waiting for), not yours (which call is in flight).
4. **A skeleton is a destination, not a slideshow.** The loading placeholder should match the final layout and transition to real data in one swap. Layout should not reflow as each piece arrives.
5. **When you touch a flow, audit its transitions.** Before shipping any page/feature change, count the distinct loading/empty/partial states a user actually sees from click to data. If it's more than one, collapse it. This applies to existing code you're modifying, not just new code.

**Threshold-based feedback (don't spin on everything).** Match the indicator to the wait, per Nielsen's response-time limits ([nngroup](https://www.nngroup.com/articles/response-times-3-important-limits/)):
- **< 0.1s** — feels instant. Show *no* indicator; just render the result.
- **0.1s–1s** — users notice but keep their flow. Still *no* spinner — a spinner that appears and vanishes inside a second is a flicker, which reads as jank and is worse than nothing. If you must, hint via cursor/subtle state, not a mounted loader. (Add a short delay before showing any spinner so fast responses never flash one.)
- **1s–10s** — show exactly one indicator for the whole action. Indeterminate (spinner/indeterminate bar) is fine when you can't measure progress.
- **> 10s** — show a **determinate** percent-done indicator *and* a clear way to cancel; reassures the user the system hasn't hung and lets them switch tasks.
- **Determinate vs. indeterminate** (Material 3 progress indicators): use a real percent-done bar whenever you can actually measure progress; fall back to indeterminate only when duration is genuinely unknown. Never show progress theater (a fake/arbitrary bar) and never stack competing indicators in one region.

**No layout shift while loading (CLS).** Reserve the space before the content arrives so nothing reflows — target CLS ≤ 0.1 ([web.dev](https://web.dev/articles/cls)). The skeleton must occupy the final element's dimensions; give images/media explicit `width`/`height` (or aspect-ratio); inject async content into pre-allocated space, never above existing content. Animate with `transform` (`translate`/`scale`), not `top`/`left`/`width`/`height`, and respect `prefers-reduced-motion`.

**This is Visibility of System Status done right, not skipped** (Nielsen heuristic #1): the user *is* kept informed — of the one thing they care about (their data is on the way), in their terms, following platform conventions (#4) — *not* of which API call is in flight. User-meaningful status, zero implementation leakage.

**The test:** could the user describe the loading experience without knowing how the feature is built? If your loading UI only makes sense to someone who's read the code, it's wrong.

**The code shapes that cause this (forbidden by construction).** Everything above describes what the user must *see*. The reason this rot keeps coming back is that the rules above govern the *output* while the failure is in the *code structure* — so an output-only rule never prevents the input. The structural causes, each as a rule:

- **A. A loading state belongs to a user action, not a data source.** Data hooks return `data | null` (or throw to an error boundary) — they do **not** each render their own spinner. Exactly **one** component, at the user-action / route boundary, owns the pending UI for the whole action. If you add a hook that returns `isLoading` while a sibling already shows a spinner for the same action, you have built a flicker-chain. Test: one user action → at most one mounted loading indicator.
- **B. Resolve the dependency chain server-side. Fetching a route's primary data in a client `useEffect` is banned.** A route's primary data is fetched in a server component / server action, streamed behind a single feature-level Suspense boundary — never in `useEffect` after a `"use client"` page mounts, and `router.push` must not land the user on an empty client page that fetches its main content on mount. `useEffect(() => { fetch… }, [])` for a route's primary data **is** a request waterfall (each component renders before it discovers its own fetch — a staircase in the network tab, one loading phase per hop). **Draw the data-dependency graph; don't choreograph component lifecycles.** The fix is *not* "make everything parallel": serialize only *genuinely* dependent calls (`b` needs a value `a` returned), and start *independent* ones together with `Promise.all`. Sequential `await`s waterfall **even in a Server Component** — `const a = await getA(); const b = await getB(a.x); const c = await getC(a.x)` runs serially though `b` and `c` are independent; start their promises first, then `await Promise.all`. Watch for *accidental* dependencies (`a` and `b` both only need an id the server already has from the session/cookie — neither needs the other). **Escape hatch:** `useEffect`-fetch is permitted *only* for secondary or interaction-triggered data (not the route's primary content), with a one-line comment justifying why it can't be server-resolved — and even then prefer a query layer (SWR / TanStack Query: shared keys, `keepPreviousData`/`placeholderData` to kill refetch flicker, dedupe, cancellation) over a hand-rolled `useState`+`useEffect` fetch. See the `react-effects` skill for the effect-correctness side (race conditions, fetch cleanup, effect chains).
- **C. One `pending`, never per-phase booleans; choose the indicator by duration, not by stage.** Do not expose multiple loading flags that mirror backend phases (`isLoading` + `isRendering`) and never swap user-facing copy between them — those booleans are the implementation, painted on screen. Collapse to one pending state; pick the indicator from the 0.1 / 1 / 10s ladder for the *whole* action. A genuinely-long (>10s) op gets its own deliberate determinate + cancelable indicator — that is a UX decision, not "expose the second boolean."
- **D. When you add a dependency to an existing flow, join its loading state — don't add a new one.** Before writing any `isLoading` / spinner / skeleton, grep the flow for the component that already owns the pending UI and hang your dependency off it. If you are copying another fetch-hook's shape (the tell is a comment like *"mirroring use-X.ts"*), that is the signal to **centralize** the fetch/loading, not duplicate it. Copy-pasted fetch hooks are how a flow accretes N spinners with no owner.

# Outerport Backend Development Guide

## Local Docker Compose Setup

Basic usage is in [README.md](README.md) ("Quick Start" and "Running multiple branches in parallel"). For the non-obvious gotchas (op-parseport not pulled in transitively, op-gpu-inference being GPU-only, the OP-tier gate on the Agent UI, the `tako-vm` Dockerfile COPY requirement, dev-stack internals), see [internal-docs/local-dev-stack.md](internal-docs/local-dev-stack.md).

## Build / Test / Lint Commands

See [README.md](README.md) — "Running Tests", "Code Quality" (per-project lint / format / typecheck), "Database migrations (drizzle)", "Updating API Documentation", and the "Manual Setup" section for service start commands (uvicorn, hyrex workers, `npm run dev`). Don't duplicate command lists here.

A few notes that the README doesn't cover:

- **op-notebook PRs**: install with `npm install --no-workspaces`. The workspace mode is convenient for testing but breaks the production docker build.
- **Drizzle**: never run `drizzle-kit generate` or `drizzle-kit migrate` on the user's behalf — tell them the command and let them execute. (See also the CRITICAL rule on `drizzle-kit push`.)

## Job Status System

The job status system provides asynchronous task tracking for long-running operations:

- **Document Upload Response**: Returns `{ document_id, job_status_id }` 
- **Job Status API**: 
  - GET `/api/v0/job-statuses/{job_status_id}` - Check job status
  - POST `/api/v0/job-statuses/{job_status_id}/cancel` - Cancel a job
- **Frontend Integration**:
  - Upload functions return job_status_id for tracking
  - Polling mechanism checks job status every 2 seconds
  - UI shows processing indicators until job completes
- **SDK Support**: TypeScript SDK includes `waitForCompletion()` method with configurable polling

## Architecture

### Component Relationship

```
┌─────────────────┐     Server Actions      ┌─────────────────┐
│   op-notebook   │ ────────────────────────▶│    op-core      │
│   (Next.js 16)  │    (never from client)   │   (FastAPI)     │
└─────────────────┘                          └────────┬────────┘
                                                      │
                                    SQLAlchemy +      │ Hyrex queue
                                    Repository        │  (PostgreSQL)
                                                      ▼
                                             ┌─────────────────┐
┌─────────────────┐                          │  op-parseport   │
│   PostgreSQL    │◀─────────────────────────│ (Hyrex worker)  │
│                 │      SQLAlchemy +         └────────┬────────┘
│  - App data     │      Hyrex task queue             │
│  - Hyrex queue  │                            Hyrex queue +
└─────────────────┘                          Redis result passing
        ▲                                             │
        │ SQLAlchemy + Repository                     ▼
┌───────┴─────────┐                          ┌─────────────────┐
│    op-core      │                          │ op-gpu-inference │
└─────────────────┘                          │  (Hyrex worker)  │
                                             └────────┬────────┘
┌─────────────────┐                                   │
│     Redis       │◀──────────────────────────────────┘
│  DB 2: GPU results (10min TTL)              NVIDIA GPU
│  + Q&A streaming │                    (PaddleOCR, YOLO, etc.)
└─────────────────┘
```

- **op-notebook** (Next.js 16): Frontend UI. All calls to op-core go through Next.js server actions (`"use server"` in `src/lib/outerport/actions.ts`) - never directly from client components. Drizzle is only for schema definitions and migrations, not runtime queries.

- **op-core** (Python/FastAPI): Lightweight shared library + API server. Contains REST API endpoints, Hyrex task **stubs**, repository pattern for DB access, and core processing modules (reasoner, summarizer, searcher, generator, extractor). Also contains the `ApiSettings` config, SQLAlchemy models, and all shared services (database, storage, generator, token tracking).

- **op-parseport** (Python/Hyrex worker): Document processing orchestrator. Contains Hyrex task **implementations**, layout detection, and document processing modules (reader, parser, layout_parser). Delegates GPU-heavy ML inference to op-gpu-inference via Hyrex tasks. **Depends on op-core** as an editable package. **NOTE**: When the user says "backend" without qualification, they usually mean op-parseport (where the actual processing happens), not op-core.

- **op-gpu-inference** (Python/Hyrex worker): Dedicated GPU inference service. Runs all computationally expensive ML models: text detection/recognition (PaddleOCR), layout detection (PPStructureV3), table detection (RT-DETR-L), orientation detection, and 8x YOLO drawing detectors. Communicates with op-parseport via Hyrex task queue + Redis result passing (DB 2). Runs as a separate container with NVIDIA GPU reservation. **Depends on op-core** as an editable package.

- **op-sandbox** (Python/Hyrex worker): Hyrex-queue-based sandboxed-Python execution worker — the sandbox backend for the agent and chart/flowchart pipelines. Drains sandbox-execution tasks from the Hyrex queue, spawns a sibling gVisor-isolated tako-vm container per task via the host Docker socket, executes the user-supplied Python code with `tako-vm-python:latest` (matplotlib / pandas / numpy / lxml pre-installed), and publishes the result to Redis DB 2 keyed by `sandbox_result:<request_id>` (a caller-generated UUID). **Depends on op-core** as an editable package. See [`internal-docs/how-op-sandbox-works.md`](internal-docs/how-op-sandbox-works.md).

- **PostgreSQL**: Serves dual purpose — application data (SQLAlchemy) AND Hyrex task queue. Hyrex is **PostgreSQL-backed**, not Redis-backed. All task enqueueing, execution tracking, and worker coordination happens through PostgreSQL.

- **Redis**: Used for (1) GPU inference result passing between op-parseport and op-gpu-inference (DB 2, 10-minute TTL), and (2) question answer streaming. Redis is **NOT** the Hyrex task broker.

- **SDKs** (public packages): `outerport-typescript`, `outerport-python` - client libraries for customers. Both are handwritten REST clients covering only a subset of the API (documents, questions, settings, job statuses, retention policies; Python additionally has folders and components). Do not assume an endpoint is SDK-reachable.

### API Domain Map (op-core routers)

All routers registered in `op-core/src/op_core/api/app.py`. The public Mintlify
docs (`docs/api-reference/`) cover only a few of these — the rest are real,
live endpoints, so search the router code, not the docs, for ground truth:

- Documented publicly: `documents`, `questions`, `settings`, `job_statuses`, `components`, `threads`
- Undocumented but live: `agent/*` (sessions, runs, personas, skills, tools, attachments, workspace), `experiments/*` (projects, systems, models, campaigns, datasets, training runs — the experiment-tracker / DOE domain), `knowledge_bases/*` (CRUD, schemas, extractions), `knowledge_graphs/*` (CRUD, entities, relationships, types, packages, extraction, archive/import, uploads), `components/schematic/*` (P&ID graph editing: annotations, arrows, nodes, segments, validations, …), `folders`, `retention_policies`, `semantic_catalog` (CFIHOS class catalog for P&ID symbol/legend classification), `schematic_templates`, `tiers`, `user`, `api_key`, `queue_status`

### Hyrex Task Pattern (Stubs vs Implementations)

Hyrex is the distributed task queue used throughout the system. It is **PostgreSQL-backed** — all task enqueueing, scheduling, retries, and worker coordination use PostgreSQL, not Redis.

Task **stubs** are defined in `op-core/src/op_core/hyrex/tasks/` with just the interface:
```python
@hy.task(max_retries=3, timeout_seconds=3600)
def process_document(context: ProcessDocumentContext):
    raise RuntimeError("stub-should not be run")
```

Task **implementations** live in `op-parseport/src/op_parseport/hyrex/tasks/` with the actual processing logic. The op-core API enqueues tasks, and op-parseport workers execute them.

GPU inference task **stubs** also live in op-core (`op-core/src/op_core/hyrex/tasks/gpu/`), with **implementations** in `op-gpu-inference/src/op_gpu_inference/hyrex/tasks/`.

All task contexts are **Pydantic BaseModel** objects for serialization:
```python
class ProcessDocumentContext(BaseModel):
    user_id: int
    job_status_id: int
    document_id: int
    generate_content: bool = True
    generate_description: bool = True
    generate_summary: bool = True
```

### Document Processing Pipeline

The core document processing follows a **fan-out / fan-in** pattern:

```
process_document (orchestrator)
  │
  ├─ Acquires file from storage, converts to PDF if needed
  ├─ Determines page count, fetches tier-based formatting options
  │
  ├─ Fan-out: spawns process_page for EACH page (parallel)
  │    │
  │    ├─ Runs layout detection (25 component classes)
  │    ├─ Transcribes text via PaddleOCR or VLM
  │    ├─ Saves VisualComponents with empty content (fast user feedback)
  │    └─ Spawns component tasks (parallel):
  │         ├─ process_table → HTML extraction + VLM refinement
  │         ├─ process_chart → VLM description + matplotlib code
  │         ├─ process_flowchart → diagram classification + Mermaid
  │         └─ process_pid → line detection + graph assembly
  │
  └─ finalize_document (fan-in)
       ├─ Aggregates all page results
       ├─ Merges multi-page tables
       ├─ Segments content by tokens for RAG
       ├─ Generates document summary
       └─ Updates job status to "completed"
```

**Idempotency**: Per-page tasks use `idempotency_key=f"job-{job_status_id}-page-{page_num}"` so retries within a dispatch dedupe at the queue layer while distinct reprocess jobs for the same document remain independent. Per-task completion is recorded in `task_dispatch_completions` (PK on `(job_status_id, task_key)` + ON CONFLICT DO NOTHING), making the fan-in counter idempotent under retries.

**Page classification & detector routing**: Every image-based page is run through a `PageClassifier` (VLM) inside `process_page`, producing a `PageType` (one of `general_document`, `mechanical_drawing`, `electrical_drawing`, `pid_drawing`, `engineering_drawing`). The result is persisted to `page_descriptions.page_type` and mapped to a layout-detector mode via `detector_mode_for_page_type` in `op-parseport/src/op_parseport/hyrex/tasks/document/page_pipeline.py`:
- `general_document` → `PaddleLayoutDetector` (standard documents, 25 component classes)
- any drawing type → `MechanicalLayoutDetector` (CAD/engineering drawings)

Users can pin a page's type from the document playground (per-page dropdown). Pinned values flow through `ProcessPageContext.page_type` (worker context) or `ReprocessingRequest.pages` (a list of `PageReprocess` entries each with an optional `page_type`) and skip the classifier on the next reprocess.

**Early saving pattern**: VisualComponents are saved to the database with empty content before VLM processing completes. This gives users fast feedback in the UI while heavy processing continues in the background.

### Repository Pattern

**CRITICAL: NEVER use direct DB access (e.g., `db.query(...)`, `session.execute(...)`) in business logic, API endpoints, or task implementations.** Always use repository functions from `op-core/src/op_core/repositories/`. Direct DB access is only acceptable inside repository implementations themselves.

Database access uses the repository pattern (`op-core/src/op_core/repositories/`):
- One repository file per entity (document.py, user.py, question.py, folder.py, etc.)
- Functions like `get_document()`, `get_document_by_public_id()`, `update_document_metadata()`
- All database operations go through repositories, not direct ORM queries in business logic
- op-parseport imports and uses these same repositories from op-core
- If a repository function doesn't exist for what you need, **add it to the appropriate repository file** rather than writing a direct query

**RepositoryFactory** — the standard way to access repositories:
```python
from op_core.services.database import get_database_session
from op_core.repositories import RepositoryFactory

with get_database_session() as db:
    repos = RepositoryFactory(db)
    user = repos.user().get_by_auth0_id(auth0_id)
    document = repos.document().get_by_public_id(doc_uuid)
    # Auto-commits on success, rolls back on exception
```

The `get_database_session()` context manager handles commit/rollback/close automatically. The factory provides typed accessors for ~30 repository types (`.document()`, `.user()`, `.question()`, `.folder()`, `.tag()`, `.job_status()`, `.agent_session()`, etc.) and exposes `.commit()`, `.flush()`, `.rollback()` for explicit transaction control.

**UUID helpers** — boundary converters for the dual-ID pattern live in `op-core/src/op_core/api/utils/uuid_helpers.py`:
- `get_user_id_from_uuid()`, `get_document_id_from_uuid()`, `get_folder_id_from_uuid()`, etc.
- These convert public UUIDs to internal integer IDs at the API boundary

### Authentication & Authorization

See `internal-docs/auth-and-authorization.md` for details on JWT (Auth0) and API Key (Unkey) mechanisms.

### Configuration System

**`ApiSettings`** (`op-core/src/op_core/config.py`) — Pydantic `BaseSettings` that controls all service behavior:
- **Database**: `postgres_user`, `postgres_password`, `postgres_host`, `postgres_port`, `postgres_db`, `postgres_require_ssl`
- **LLM providers**: `openai_api_key`, `azure_openai_endpoint`/`api_key`, `gemini_api_key`, `anthropic_api_key`
- **Model selection**: All 21 model roles are defined in `MODEL_ROLE_DEFINITIONS` (a `Dict[str, ModelRoleDef]` at module level in `config.py`). Each role has a description, default value, and allowed values — used for field defaults, validation, API responses, and the UI. Roles: `reasoning_model`, `retrieval_model`, `agent_model`, `chart_generation_model`, `flowchart_generation_model`, `diagram_classification_model`, `description_generation_model`, `vlm_ocr_model`, `schema_extraction_model`, `table_baseline_model`, `table_refinement_model`, `summarization_model`, `page_classification_model`, `legend_remap_model`, `cfihos_symbol_classification_model`, `legend_extraction_model`, `entity_field_extraction_model`, `agent_visual_inspection_model`, `component_qa_model`, `kb_schema_generation_model`, `kb_extraction_model`.
- **Per-user model preferences**: Stored in `settings.preferred_models` (JSONB column). Chainsaw/OP tier users can override any model role via the Settings UI. Preferences are resolved at runtime via `ApiSettings.resolve_model(role, preferred_models)`, which falls back to the global config default if the preference is invalid or missing, and to a non-Gemini alternative if the Gemini API key is not configured.
- **Auth**: `use_auth`, `auth0_domain`, `auth0_api_audience`, `unkey_api_id`/`root_key`
- **Storage**: `storage_backend` (`local`/`aws`/`azure`/`gcp`), bucket names, prefixes
- **Feature flags** (all in `config.py`): pipeline — `use_ocr`, `use_transcription_on_all_pages`, `use_parseport`, `use_vlm_for_parseport`, `use_table_parser`, `use_flowchart_parser`, `use_text_line_gap_fill`, `validate_mermaid_syntax`; agent/sandbox — `agent_workspace_persistence_enabled` (S3 workspace sync); observability — `use_logfire`, `use_activity_log` + `activity_log_path` (activity dashboard audit log); UI experiments — `playground_show_{flowchart,table,graph,document,pid}`
- **Tier override**: `override_usage_tier` (`free`/`trial`/`basic`/`premium`/`frieren`/`chainsaw`/`pid`/`op`)

Loaded from environment variables → `.env` / `.env.local` / `.env.development` / `.env.test` / `.env.production` → defaults.

**`ParseportSettings`** (`op-parseport/src/op_parseport/config.py`) — Extends `ApiSettings` with parseport-specific settings (Hyrex worker timeouts, flowchart VLM image resolution). Model selection fields have been consolidated into `ApiSettings`.

### Generator Service (LLM Abstraction)

**Location**: `op-core/src/op_core/services/generator.py`

Thread-safe singleton that caches LLM generator instances by config:
```python
from op_core.services.generator import get_generator_service

generator_service = get_generator_service()
generator = generator_service.get_generator(
    provider="openai",  # or "gemini"
    model_name="gpt-4.1",
    temperature=0.0,
    max_tokens=4000,
)
result = await generator.generate(task_type="...", step="...", prompt="...")
```

Implementations: `OpenAIGenerator` (GPT-4, Azure OpenAI) and `GeminiGenerator` (Google Gemini, VertexAI). All generators inherit from `BaseGenerator` with `generate()` and `generate_stream()` methods.

### Frontend API Call Pattern

**CRITICAL**: op-notebook NEVER calls op-core directly from client components. All API calls must go through:
- Server actions in `src/lib/outerport/actions.ts` (marked with `"use server"`)
- Server components that fetch data during SSR

### Key Processing Patterns

- **Async Processing**: Hyrex queue prevents blocking on long operations
- **Multi-Model Support**: OpenAI, Azure OpenAI, Gemini, vLLM integration
- **Visual Understanding**: PaddleOCR for text detection/recognition, layout detection for document structure
- **Retention Policies**: Automated document lifecycle management

## Code Style Guidelines

- **Python**: Type hints required, Pydantic for models, SQLAlchemy for DB. Use `Optional[X]` for nullable types - do NOT use `X | None`, `Union[X, None]`, or overly complex nested generics like `List[List[...]]` when simpler types suffice
- **TypeScript**: Strict typing, camelCase for variables/functions, PascalCase for classes/interfaces
- **Class methods vs helper functions**: A method should only exist on a class if it:
  1. **Accesses internal state** (`self`/`this`) - it genuinely needs the instance
  2. **Is part of the public interface** - external callers actually use it

  Everything else should be a standalone helper function. This keeps classes focused, makes logic easier to test in isolation, and prevents bloated classes full of "private" methods that don't need `self`.
- **No `_` prefix for module-private functions**: Do NOT add an underscore prefix to functions just because they are only used within the module. The `_` prefix convention is for signaling privacy on class members — at the module level, if a function isn't exported or isn't in `__all__`, it's already effectively private. Unnecessary `_` prefixes add visual noise without value.
- **No re-export aliases for backward compatibility**: Do NOT create type aliases or re-exports like `export type OldName = NewName` to keep old names working. When renaming a type or moving it to a new location, update all consumers to use the new name directly. Compat aliases accumulate confusion (two names for one thing) and never get cleaned up.
- **Error handling**: Use try/catch blocks with specific error types
- **Imports**:
  - Group standard library, third-party packages, and local imports with blank lines
  - **NEVER use inline imports** - all imports must be at the top of the file
  - Bad: `from op_core.services.storage import get_storage_client` inside a function
  - Good: Import at the top of the file
- **Documentation**: Docstrings for Python functions, JSDoc for TypeScript
- **Structure**: Follow domain-driven design with clear separation of concerns
- **Pipeline/task structure**: Separate deciding from doing — pure functions compute plans/values, the task/endpoint body (shell) owns all DB writes, commits, and enqueues. See [internal-docs/design/functional-core-imperative-shell.md](internal-docs/design/functional-core-imperative-shell.md)
- **Testing**: Write unit tests for each module, strive for high test coverage
- **Dependencies**: Always prefer Python/Node packages over system dependencies (apt-get) when possible. If system dependencies are unavoidable, document them clearly in a SYSTEM_REQUIREMENTS.md file
- **Debugging**: DO NOT add console.log statements to the code. Use proper error handling and return values instead

## Tier Settings Consistency

**When modifying FRIEREN (or other tier) formatting options, check ALL locations where these settings are applied:**

Formatting options for tables, flowcharts, and text are set in multiple places that must stay in sync:

1. **Component Playground (POST)**: `op-core/src/op_core/api/endpoints/components.py` - POST endpoint
2. **Component Playground (PUT)**: `op-core/src/op_core/api/endpoints/components.py` - PUT endpoint
3. **Document Playground**: `op-parseport/src/op_parseport/hyrex/tasks/document/process_document.py`

**When adding a new formatting option:**
- Add it to the options class in `op-core/src/op_core/core/options.py`
- Update ALL three locations above with the new setting for each relevant tier
- Grep for `TierLevel.FRIEREN` (or the relevant tier) to find all places

**Example settings that must be consistent across flows:**
- `TableFormattingOptions`: add_border_attribute, wrap_text_in_paragraphs, flatten_row_column_spans, collapse_multi_header_rows, detect_lists_in_cells, include_styles, remove_thead_tbody, describe_visual_cells
- `FlowchartFormattingOptions`: use_undirected_for_non_arrows, use_dotted_for_callouts, use_edge_labels, include_styles, use_frieren_naming
- `TextFormattingOptions`: use_semantic_html, detect_lists, exclude_header_footer_images

## Design for Features, Not Implementation

Write tests and interfaces that describe **what** the system does, not **how** it does it internally.

**The Neural Network Test**: Could this test/interface still work if the entire implementation was replaced with an opaque neural network? If a test breaks because you refactored internals (not behavior), it's testing the wrong thing.

**Concrete examples**:

| ❌ Don't | ✅ Do |
|----------|-------|
| Test that the repository calls `session.query()` with correct filters | Test that `get_user_documents()` returns only documents the user owns |
| Test that `_parse_header()` helper returns specific dict structure | Test that document processing extracts the correct title and metadata |
| Assert internal state (`assert len(service._cache) == 3`) | Assert on outputs and behavior (`assert result.total_count == 3`) |
| Test the SQL query structure | Test that unauthorized users can't access other users' data |
| Write tests per private method | Write tests per user-facing feature or business rule |

**Why this matters**: Good code is easy to delete. If you swap Postgres for DynamoDB, refactor a class into functions, or restructure modules—tests on business logic keep passing, tests on implementation break.

## API Security Guidelines

- **CRITICAL**: Every API endpoint MUST verify user authorization for ALL resources accessed
- **Document Access**: Always check if the user has permission to access documents before returning data
  - Check document ownership or sharing permissions
  - This applies to direct document access AND any endpoint that returns document-related data (reviews, contexts, etc.)
- **Multi-tenancy**: Ensure queries are properly filtered by user's organization/tenant
- **Never Trust IDs**: Always validate that resource IDs provided by users belong to their authorized scope
- **Authorization Pattern**: 
  ```python
  # Always verify document access before using document data
  document = await get_document_if_authorized(document_id, user_id)
  if not document:
      raise HTTPException(403, "Access denied")
  ```
- **Test Authorization**: Write tests that verify unauthorized users cannot access resources

## Database ID Design Pattern

This codebase follows a dual-ID pattern for security and consistency:

- **Internal ID (`id`)**: Integer primary key used internally for database relationships
  - NEVER exposed in API responses or accepted in API requests
  - Used in all association tables (e.g., `document_user_association`, `document_tag_association`)
  - Used for all foreign key relationships
  
- **Public ID (`public_id`)**: UUID used for all external API interactions
  - The ONLY ID exposed in API responses
  - The ONLY ID accepted in API requests
  - Must be converted to internal ID before any database operations

**Key Rules**:
1. Association tables ALWAYS use internal integer IDs, not UUIDs
2. When creating associations, use `document.id`, NOT `document.public_id`
3. When filtering/querying associations, convert UUID to internal ID first
4. API endpoints must convert between public_id (UUID) and id (integer) at the boundary

**Common Pattern**:
```python
# Convert UUID to internal ID
document = db.query(Document).filter_by(public_id=document_uuid).first()
# Use internal ID for associations
association = DocumentUserAssociation(
    document_id=document.id,  # NOT document.public_id!
    user_id=user.id
)
```

**Schema definitions & migrations**: The **source of truth** for the database schema is the TypeScript Drizzle schema in `op-notebook/src/db/schema.ts`. All migrations are managed through Drizzle. The SQLAlchemy models in `op-core/src/op_core/schemas.py` are a **copy** that must be kept in sync manually — they are used for runtime DB access in Python but do NOT drive migrations. Pydantic response models live in both `schemas.py` and `op-core/src/op_core/api/models/responses.py`.

## Supply Chain Security

All dependencies enforce a 7-day minimum release age. When adding or updating dependencies:

- **Python (uv)**: `exclude-newer = "P7D"` in `[tool.uv]` of every `pyproject.toml`. If adding a new Python project, include this setting. If the project uses non-PyPI indexes that lack PEP 700 upload-time metadata (e.g., PyTorch, PaddlePaddle), add `exclude-newer-package` exemptions.
- **npm**: `min-release-age=7` (days) in `.npmrc` files. Due to [npm/cli#9005](https://github.com/npm/cli/issues/9005), Docker builds must use `npm ci --min-release-age=0` (age enforcement happens at lockfile generation time, not install time).
- **Docker**: Base images are pinned to specific version tags (e.g., `python:3.11.15-slim-trixie`). Updated manually.
- **Lock maintenance**: `.github/workflows/lock-maintenance.yml` runs every Monday. First Monday of the month: full update of all lock files. Other Mondays: security-only (skips PR if no Dependabot alerts are resolved). Creates one PR per subproject directory, with resolved security alerts listed in the PR body.
- **Security alerts**: Dependabot security alerts are enabled (notifications only). Security updates and version update PRs are disabled — lock maintenance handles security patches through lock file updates within existing constraints.

## CRITICAL: Loading .env Files for Shell Commands

**NEVER use `source .env`** — it doesn't export to child processes. Use `export $(grep -E "^[A-Z_]+=" .env | xargs)` instead, or scope it with e.g. `grep -E "^AWS_"` for specific prefixes.

## Database Schema Management

Schema source of truth is `op-notebook/src/db/schema.ts`. Workflow:

1. Edit `op-notebook/src/db/schema.ts`
2. `cd op-notebook && npx drizzle-kit generate` — creates the next migration file (review the SQL; some changes need manual edits, e.g. SERIAL → IDENTITY)
3. `cd op-notebook && npx drizzle-kit migrate` — applies pending migrations

Never delete or modify a migration file that's already been applied, and never use `drizzle-kit push` (see the CRITICAL rule above).

**IMPORTANT**: For rebasing branches whose migrations have gone stale against `main`, and for debugging drizzle-kit's silent failure mode locally, see [internal-docs/drizzle-migrations.md](internal-docs/drizzle-migrations.md).

## Git Commit Rules

**NEVER use `--no-verify` flag when committing**. If pre-commit hooks fail:
1. Fix ALL errors (linting, type errors, tests) before committing
2. Run the appropriate commands to ensure code quality:
   - TypeScript: `npm run lint` and `npm run typecheck`
   - Python: `uv run --frozen ruff check . --fix` and `uv run --frozen mypy`
   - Rust: `cargo fmt` and `cargo check`
3. Only commit after all checks pass successfully

