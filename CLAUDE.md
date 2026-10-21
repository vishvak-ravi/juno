# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## CRITICAL: Explain concretely, from first principles

Every statement must name a real, pointable thing — a file, function, object, field, or value. A vague phrase used as an explanation (`share a net`, `the view`, `continuity`, `the pipeline`) is banned unless defined in the same breath in terms of actual code (not "the docs don't share a net" but "no `Coupling` object references both `TB-P.t1` and `TR_CTRL`"). When stating a limitation, trace the actual path step by step and mark each step checked/unchecked with the concrete reason. If a sentence would only make sense to someone who already knows what you mean, replace it with the concrete object.

## CRITICAL RULES TO REMEMBER

Note these down as critical rules that are to be followed in this repository at all times.

### CRITICAL: No Development History in Code

**Comments, docstrings, error messages, test names and `config.yaml` comments describe the code as it is now, never how it got there.** Banned: "deprecated", "legacy", "used to", "no longer", "was renamed/removed/moved", "replaces the old X", "kept for compatibility", "for now", "not yet", "so far", dates, and any mention of a route, field or type that no longer exists.

- Bad: `# this route was removed; clients read sessions through events instead`
- Good: nothing here; document the events route where it is defined
- Bad: `// only shown in the overlay for now`
- Good: `// shown in the overlay; drives no role`

History, plans, status and decisions go in `docs/` (dated entries) or in the commit message. A pinned version of an outside file (`scorer.py at <commit>`) is a reference, not history.

### CRITICAL: Documentation Layout

- **`README.md`**: what runs where (the support matrix) and the commands, nothing else. No prose, status or dates. When a process, platform or command changes, update the matrix and commands in the same change.
- **`docs/`**: everything else: `requirements.md`, `decisions.md` and `status.md` (dated entries), design notes per area, and `docs/history/` for superseded plans.
- **No other `.md` files**: no per-folder READMEs, no plan files at the root.

### CRITICAL: DO NOT CREATE DOCUMENTATION FILES

**NEVER create .md files unless the user EXPLICITLY requests them.** This includes:
- Architecture documents
- Implementation guides
- README files
- Migration plans
- Design documents
- Any other markdown documentation

**Instead**: Provide explanations, architecture recommendations, and code examples directly in chat responses. The user will ask you to create files if they want them saved. When they do, the file goes in `docs/` (see Documentation Layout).

Keeping existing docs accurate is not creating docs: when a change makes `README.md` or a file in `docs/` wrong, fix it in the same change.

### CRITICAL: No User-Specific Examples in Code

**When writing docstrings, prompts, comments, or code examples, NEVER copy the user's specific test cases, queries, or scenarios from the conversation.** Always use generic, illustrative examples — or omit examples entirely if the instruction is clear without them. This applies to error messages, variable names, and any other place where you might be tempted to parrot back what the user just said. Especially never in prompts.

### CRITICAL: Check Internal Docs First

**Before doing ANYTHING, check `docs/` first.** Requirements, decisions, per-area design notes (capture, client) and past plans are there. Search them before searching the codebase or proposing solutions. The answer is probably already written down.

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
   - What are the processing stages? (e.g., recorder → upload → annotation → prediction)
   - What modules/services are involved?
   - How does data flow through the system?

2. **Map where the feature intersects the pipeline**
   - At which stages could this feature be used?
   - What are the entry points and consumers?
   - Are there multiple independent usages?

3. **Verify with broad searches**
   - Search for the feature across the entire codebase to confirm understanding
   - Don't assume a config file or single location is exhaustive

**Bad:** Find `annotate.model` in `config.yaml` → assume that's all model usage

**Good:** Understand the flow from recorder to prediction → see that models are called for annotation (`annotate.model`), chat (`llm.base_url`), prediction (`predict`), benchmarks (`scripts/benchmark_prediction.py` predictor) and the judge (`grade.model`: `scripts/grade_action_captions.py`, `predict.reward`) → search for every model call → trace actual usage

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
- Code imported from Tada (`napsack`) lives in the installed package, not in this repo - read it there

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

- **Models and knobs shared by every device**: add a key to the matching
  section of `config.yaml` (with a comment saying what it does) and the
  field to the settings model in `packages/core/src/juno/config.py`, the same way
  `annotate.model` and `predict.past_len` are defined.
- **Values that differ per device** (server host, device id, API keys): read
  from `.env`, listed in `.env.example`.
- **Per-request knobs**: a request body or query field with a default taken
  from `config.yaml`, never a literal in the function body.

Hardcoded models / thresholds defeat the configuration system and turn
every model swap into a code change.

When in doubt, look at how the neighbouring code reads its model from
`juno.config`.

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

### CRITICAL: Agent Tool Data Accessibility

When adding or modifying agent tools, every tool parameter must be answerable by the LLM from its context (system prompt, conversation history, or prior tool results). If a parameter requires a server-side identifier (UUID, internal ID, storage key, etc.) that the LLM has no way to discover, the tool WILL silently fail at runtime.

**Checklist for every tool parameter:**
1. Can the LLM determine this value from the conversation? (e.g., user typed it)
2. Is it surfaced in the system prompt or message metadata? (e.g., attachment UUIDs)
3. Can it be obtained from a prior tool call's output? (e.g., search → use result ID)
4. Is it a free-form value the LLM generates itself? (e.g., output_format="mermaid")

If none of these apply, the tool is broken — fix the data pipeline before shipping the tool.

**Common failure cases:**
- Annotation references: LLM sees a caption but not the `annotation_id` that `get_annotation` needs
- Device or session IDs: tool needs a `device_id` or session id the LLM was never told about

**Where identifiers get surfaced:**
- Tool definitions and results in `packages/server/src/server/agent/tools.py` and `packages/server/src/server/agent/mcp.py` — return IDs that downstream tools need
- System prompts in `packages/server/src/server/agent/prompts/` — include context the tools rely on (devices, time range)

### CRITICAL: Never Make Users Watch the Implementation Load

**Loading states must reflect user-meaningful progress, not implementation stages.** The user does not know — and must never be made to see — that a page is made of 4 API calls, N module loads, or a chain of async transitions. From the user's seat there is exactly one transition: `click → loading → data`. Anything finer is leaking your architecture into their eyes.

The failure looks like this: each intermediate fetch/hydration step renders its own distinct spinner, skeleton, or "loading X…" message, so the user watches the implementation assemble itself piece by piece. That is a bug, not a feature.

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

# Juno Development Guide

## Where things are

Commands and the per-platform support matrix are in [README.md](README.md);
requirements, decisions, status and design notes are in [docs/](docs/).

- `packages/core/src/juno`: what the server and the recorder share: `juno.config` loads `config.yaml`; `juno.sessions` is the session id and row format; `juno.events` the event ids; `juno.control` the recorder's report
- `packages/capture/src/capture`: the recorder (`python -m capture`); platform sources in `capture/sources/`; `capture.parent_watch` stops it when the client that started it dies
- `packages/server/src/server`: FastAPI API server (`python -m server`, `server.port` in `config.yaml`); routes in `routes/`; the chat model, its tools and MCP in `agent/` (`llm.base_url`, `agent/tools.py`, `agent/mcp.py`)
- `packages/server/src/annotate`: annotation of recorded chunks with `annotate.model`
- `packages/server/src/predict`: next-action predictor and benchmark rows
- `apps/desktop/`: Electron desktop client (`src/main`, `src/preload`, `src/renderer`); it starts the recorder
- `apps/web/`: the phone page (`index.html`), the drawing page (`draw.html`) and, in `src/`, the modules the phone page and the desktop browser page share (`api.js`, `views.js`, `markdown.js`); the server serves them as they are. The desktop window is `apps/desktop/src/renderer/index.html`
- `scripts/`: install scripts, dataset and benchmark CLIs
- Settings: `config.yaml` (shared by every device), `.env` (this device: `JUNO_SERVER_HOST`, `JUNO_DEVICE_ID`, `GEMINI_API_KEY`), both at the checkout root

The API server, the recorder and the model server (vLLM at `llm.base_url`) are
separate processes that usually run on different machines.

## Match Tada

Capture and prediction follow Tada (`napsack`, its prompted predictor and grader)
exactly. Do not add signals or behavior Tada lacks; see [docs/decisions.md](docs/decisions.md).

## Code Style Guidelines

- **Python**: Type hints required, Pydantic for models. Use `Optional[X]` for nullable types - do NOT use `X | None`, `Union[X, None]`, or overly complex nested generics like `List[List[...]]` when simpler types suffice
- **JavaScript** (`apps/desktop/`): camelCase for variables/functions, PascalCase for classes
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
  - Bad: `from juno.config import load_config` inside a function
  - Good: Import at the top of the file
- **Documentation**: Docstrings for Python functions, a leading comment for JavaScript modules
- **Structure**: Follow domain-driven design with clear separation of concerns
- **Pipeline/task structure**: Separate deciding from doing — pure functions compute plans/values, the route or process body (shell) owns all file writes, model calls and subprocesses.
- **Testing**: Write unit tests for each module, strive for high test coverage
- **Dependencies**: Always prefer Python/Node packages over system dependencies (apt-get) when possible. If system dependencies are unavoidable (evdev's C build, PyGObject, GStreamer's PipeWire plugin, bubblewrap), list them in the README setup.
- **Debugging**: DO NOT add console.log statements to the code. Use proper error handling and return values instead

## Design for Features, Not Implementation

Write tests and interfaces that describe **what** the system does, not **how** it does it internally.

**The Neural Network Test**: Could this test/interface still work if the entire implementation was replaced with an opaque neural network? If a test breaks because you refactored internals (not behavior), it's testing the wrong thing.

**Concrete examples**:

| ❌ Don't | ✅ Do |
|----------|-------|
| Test that the store calls `open()` with a specific path | Test that `GET /events?device=` returns only that device's events |
| Test that a helper returns a specific dict structure | Test that a recorded session passes `capture.check` |
| Assert internal state (`assert len(service._cache) == 3`) | Assert on outputs and behavior (`assert result.total_count == 3`) |
| Write tests per private method | Write tests per user-facing feature or business rule |

**Why this matters**: Good code is easy to delete. If you swap JSON files for a database, refactor a class into functions, or restructure modules—tests on business logic keep passing, tests on implementation break.

## Supply Chain Security

Dependencies must be at least 7 days old. When adding or updating dependencies:

- **Python (uv)**: `exclude-newer = "P7D"` in `[tool.uv]` of `pyproject.toml` and any new `pyproject.toml`. If the project uses non-PyPI indexes that lack PEP 700 upload-time metadata (e.g., PyTorch, PaddlePaddle), add `exclude-newer-package` exemptions.
- **npm**: `min-release-age=7` (days) in `apps/desktop/.npmrc`.

## CRITICAL: Loading .env Files for Shell Commands

**NEVER use `source .env`** — it doesn't export to child processes. Use `export $(grep -E "^[A-Z_]+=" .env | xargs)` instead, or scope it with e.g. `grep -E "^AWS_"` for specific prefixes.

## Git Commit Rules

**NEVER use `--no-verify` flag when committing**. If pre-commit hooks fail:
1. Fix ALL errors (linting, type errors, tests) before committing
2. Run the appropriate commands to ensure code quality:
   - Python: `uv run pytest` at the checkout root
   - Client: `npm ci` in `apps/desktop/`
3. Only commit after all checks pass successfully

