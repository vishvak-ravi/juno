# Juno requirements

A personal system that knows what I'm doing across my devices, recommends what
to do next, and dispatches agents to do it.

> Status: requirements draft. Items marked **(proposed)** are suggestions to
> confirm, not decisions.

## 1. Problem

What I'm solving, in order of importance:

1. **I have to come up with every task myself.** Nothing looks at what I've been
   doing and proposes the next piece of work.
2. **My assistants don't share a memory.** What I talk to on my phone and what I
   talk to on my computers know different things about me.
3. **Work only happens while I'm at a keyboard.** I want to approve a plan from
   my phone and see a PR later.

What I am *not* solving: building a better coding agent, chat client, or PR
review tool. Those exist and Juno is **additive** to them.

## 2. Core loop

```
capture ──> shared context ──> recommend ──> accept ──> dispatch ──> result
(computers)   (my server)     (overnight)    (phone)    (Codex)     (GitHub PR)
                                   ^            │
                                   └── feedback ┘
```

Everything in the MVP serves this loop. Anything that doesn't is cut (§6).

**Terms.** *Intent* and *next action* mean the same thing: what I will do next,
as predicted from captured context (Tada's next-action prediction, NAP). The
*intent model* is that predictor and is scored against what I actually did next.
*Recommendation* is a separate step: it decides which items to put in the feed,
and in what form. It is scored by K1, not by intent accuracy.

## 3. Success criteria

| # | KPI | Type | Definition |
|---|-----|------|------------|
| K1 | Recommendation accept rate | Metric | accepted / shown, for recommended items |
| K2 | Useful mobile app | Binary | I can read the feed, accept, and dispatch from my phone |
| K3 | Same knowledge everywhere | Binary | Phone and computers answer from the same context about me |
| K4 | No frontier-model dependency | Binary | The core loop runs on my own model; frontier models are optional executors |

K1 is the only one to optimize. K2–K4 are pass/fail gates for the MVP.

## 4. Functional requirements (MVP)

| # | Requirement | Serves |
|---|-------------|--------|
| F1 | **Capture**: computers (Mac, Linux) record context events | K3 |
| F2 | **Consolidate**: one user-designated server stores the shared context; every client reads from it | K3 |
| F3 | **Predict + recommend**: the intent model predicts next actions from context; a separate recommendation step turns them into feed items (intents or plans), computed in batch (overnight) | K1 |
| F4 | **Feed**: phone and desktop show the recommended feed; each item can be accepted or rejected | K1, K2 |
| F5 | **Dispatch**: accepting an item starts an agent session (Codex) in a workspace and returns a link to the session / resulting PR | K2 |
| F6 | **Feedback log**: every shown / accepted / rejected recommendation is recorded | K1 |
| F7 | **Chat**: I can talk to my own model from any device; it uses the shared context and can dispatch work | K2, K3, K4 |
| F8 | **Devices**: register devices and designate the server | K3 |

## 5. Non-functional requirements

| # | Requirement |
|---|-------------|
| N1 | **Single user, many devices.** Not multi-tenant. |
| N2 | **Data locality.** Context lives where I choose (my server); it is not sent to third parties except what a dispatched agent needs. |
| N3 | **Model independence.** Intent + chat/router run on my own model. Executors are swappable. |
| N4 | **Additive.** Juno never blocks using the underlying tools directly (open the Codex session in the ChatGPT app, review the PR in the GitHub app). |
| N5 | **Batch is fine.** Recommendations have no real-time requirement; only chat is interactive. |
| N6 | **Offline tolerant.** Event writers buffer and sync later; the server may be a home machine that is sometimes unreachable. |
| N7 | **iOS constraints.** The phone is a read/act client, not a collector. No background monitoring. |
| N8 | **Measurable from day one.** K1 must be computable from F6 before any model work starts. |
| N9 | **Setup can be hard for now.** I'm the only user. |

## 6. Scope

### In (MVP)

- Event capture on Mac + Linux
- One server, one context store
- Baseline intent model, overnight batch
- Recommended feed with accept / reject
- Dispatch to Codex, result as a PR
- Chat with my own model over the shared context
- Minimal device registration

### Out (cut, with reason)

| Cut | Why |
|-----|-----|
| iPhone context collection | Hard to track on Apple mobile (N7) |
| iPad as event writer **(proposed)** | Two capture platforms are enough to prove K3 |
| Custom executor agent | Codex already does this (N4) |
| Chat detail view with tool calls for agent sessions | ChatGPT/Codex app already shows the session (N4) |
| PR review UI | GitHub app (N4) |
| Choosing *where* context is stored and *where* agents work, per device | One server + one workspace first **(proposed)** |
| Event timeline view | Debug tool, not part of the loop **(proposed)** |
| Dashboard beyond the feed | Feed + device list cover K2 **(proposed)** |
| Super easy setup | N9 |

### Later (after the loop works and K1 has a baseline)

1. Improve the intent model over baseline, measured by Tada's next-action
   score (LLM judge against the actions that actually followed)
   - build an intent dataset at scale (validate the grader model)
   - train / val on it
2. Grader model: supervises the intent model using captions + time
3. Captioning model
4. Online learning from the feedback log (F6)
5. Multiple agents proposing plans in parallel
6. Flexible storage / workspace placement per device
7. Local annotator and grader, replacing Gemini (see below)

Current model choices are in [`decisions.md`](decisions.md).

## 7. Open questions

1. **K1 definition**: does "accepted with edits" count? Is the denominator
   *shown* or *generated*? What accept rate makes the feed worth opening?
2. **Mobile client**: native iOS app or web app? K2 only needs feed, accept,
   dispatch, chat.
3. **Context freshness**: how stale can shared context be across devices before
   K3 fails — minutes or a day?
4. **Recommendation unit**: a predicted intent shown as is, or a full plan an agent can run
   unattended?
5. **Domain**: coding work only (result = PR), or any intent?
6. **"My own model"**: which one, and where does it run — the server?

## 8. Notes on "how" (parked until §1–§7 are settled)

- Device roles: *event writer* (capture apps), *agent workspace* (Mac, or a
  compute server over SSH), *server* (context store + model router).
- Models: intent and captioning follow the NAP series I/O; grader trains the
  intent model; executor is a general agent.
- Reference implementation: [Tada](https://github.com/GeneralUserModels/tada) (General User Models) — see
  [Learning Next Action Predictors](https://arxiv.org/abs/2603.05923) and
  [Creating General User Models from Computer Use](https://arxiv.org/abs/2505.10831).

<details>
<summary>Original notes</summary>

```
goals
1. collect 'context' on iPhone (OOS, hard to track on apple mobile), iPad, Mac, and Linux
2. allow users to designate a 'server' to consolidate shared context and run a model router
2a. users should be able to select WHERE their context (device/cloud) is stored and WHERE an agent can work
2b. device roles: 'event writer' (mac, ipad, linux apps), agent workspaces (mac, compute server via SSH),
2c. bundles/apps: iOS (monitoring + goodies), lightweight
3. models: intent model (follow 'nap' series I/O), captioning model (follow 'nap' series I/O), grading model (trains intent model with supervision from captioning + time), executor (multipurpose agent)
4. views: chat list, chat detail view (markdown, show tool calls), device management (CRUD, server designation, 'model' selection), event timeline

nice to haves
1. improve the intent model from baseline
a. create intent dataset at scale (validate grader model)
b. train/val on intent dataset
2. online learning
3. improve recommendations from baseline
a. increase recommendation acceptance rate
4. super easy to setup
```

</details>
