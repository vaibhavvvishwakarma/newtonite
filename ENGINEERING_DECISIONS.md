# Engineering Decisions

This document explains the most important decisions behind OpsDesk, the options I considered, and the trade-offs I accepted.

---

## Assumptions I made

The brief leaves these open on purpose. These are my answers; I would confirm the **bold** ones with a product manager first.

1. **A work item belongs to exactly one team.** That team's roles decide who can see and change it. Cross-team work is done by creating a linked item in the other team (not built yet).
2. **Roles are per team:** `viewer` (read), `member` (create, edit, comment, claim, work on own items), `lead` (everything in the team, including assigning others and approving). A person can be a lead in one team and a viewer in another.
3. **Every item goes through approval before it is resolved:** Open → In progress → Awaiting approval → (lead) Resolved → Closed. Leads can send work back. In reality, low-risk tasks might skip approval; I'd make this a per-type setting.
4. **Any member of the team can edit an item's fields** (not only the assignee), because the whole team often adds details during an incident. Status changes are limited to the assignee or a lead.
5. **A user cannot approve their own work unless they are a lead.** A lead can approve their own items. A strict four-eyes rule (nobody approves their own item) would be a one-line change in `workflow.py`.
6. **Nothing is deleted.** Items end in `closed`, and history is append-only, for accountability.

---

## Decision 1: PostgreSQL does the hard work (locks, constraints, transactions)

**Context.** Most of the risky behaviour in this brief (two people claiming at once, stale edits, retries) is about *concurrency and consistency*.

**Options considered.**
- Handle it in application code (in-memory locks, checks before writes).
- Use Redis for locks and a queue.
- Use PostgreSQL features: row locks, unique constraints, transactions.

**Choice.** PostgreSQL only. Every write request runs in **one transaction** that:

1. locks the item row (`SELECT … FOR UPDATE`);
2. checks permission, version and workflow rules against the *locked, current* row;
3. updates the item, inserts the activity event, and inserts any outbox job;
4. commits.

**Why.** In-memory locks break as soon as there are two API instances. Redis adds a second system that can disagree with the database. A row lock in Postgres is correct no matter how many API servers run, and it's simple to explain: *requests for the same item wait in line; requests for different items run in parallel.*

**Trade-off.** Requests for the *same* item are serialized. That is fine here (an item is touched by a few people, not thousands per second). A very hot row would need a different design.

---

## Decision 2: Two kinds of concurrency control for two different problems

**Context.** "Two users try to take the same item" and "a user saves changes based on an old screen" are different problems.

**Choice.**

| Situation | Mechanism | What the user sees |
|---|---|---|
| Two people click **Claim** | Row lock + *"is it still unassigned?"* check inside the lock | One wins; the other gets `409 already_claimed` and the UI rolls back |
| Editing / moving / assigning from a stale screen | **Optimistic locking**: every item has a `version`, and the client sends the version it saw. If it doesn't match → `409 version_conflict` with the latest item | Banner: *"This item was changed by someone else"* with the latest data shown |

**Why claim doesn't need a version.** Claiming only depends on one fact ("nobody owns it"), which the server checks itself. Requiring a version would make a claim fail just because someone added a comment or changed the title, which is a needless conflict.

**Why comments don't bump the version.** A comment doesn't change the item's fields, so it shouldn't make someone else's edit fail.

**Alternative considered.** A single conditional `UPDATE … WHERE assignee_id IS NULL` (no explicit lock) also works for claim. I used the lock-then-check pattern everywhere so that *every* write follows the same, easy-to-review shape.

**Trade-off.** Optimistic locking means the second editor has to redo their change. Field-level merging (like Google Docs) would avoid that, but it is far more complex. For operational records, "tell the user clearly and show them the latest" is safer than silently merging.

---

## Decision 3: History is written in the same transaction as the change

**Context.** "Important actions should not silently disappear," and management needs to know *what changed and why*.

**Options considered.**
- Database triggers that write an audit row automatically.
- Writing history from the application, in the same transaction.
- Event sourcing (history is the source of truth, and the item is derived from it).

**Choice.** An append-only `activity_events` table (`kind` + JSON `data`), written by the service layer **inside the same transaction** as the change. Comments are activity events too, so the item page shows one timeline.

**Why.** It is impossible for the item and its history to disagree: if the history insert fails, the change rolls back. Writing it in the application (not a trigger) lets us record *meaning* ("claimed", "approved", "assigned to Bob") and the actor, not just "row changed".

**Trade-off.** Developers must remember to call `record()` in every new write path. Event sourcing would guarantee it, but it is much heavier for a one-day build. The tests check that every action produces the expected events and that rejected actions produce none.

---

## Decision 4: Authorization in one server-side module, scoped by team

**Context.** "Not every user should be able to perform every action," and hiding buttons is not enough.

**Choice.**
- Roles live in `memberships (user_id, team_id, role)`.
- **All** permission rules are in `permissions.py`, with a role × action matrix at the top of the file.
- Every service function checks permission against the **item's team**, after loading the item.
- **List queries always include `team_id = ANY(my_teams)`**, so no filter can leak another team's items.
- Roles are loaded from the database **on every request**, not baked into the token, so removing someone from a team takes effect immediately.

The frontend reads the same roles only to hide buttons. The tests call the API directly to prove the server refuses.

**Trade-off.** One extra small query per request (roles). That's cheap with the primary-key index, and worth it for instant revocation. I return `403` (not `404`) for items in other teams, which reveals that an ID exists. For a sensitive domain I would switch to `404`.

---

## Decision 5: Secondary work goes through a transactional outbox

**Context.** Notifications don't need to block the user's request, but they must not be lost, sent for changes that rolled back, or sent twice.

**Options considered.**
- Send inline in the request: slows the request, and if the commit fails afterwards we've notified about something that didn't happen.
- Fire-and-forget background thread: lost on crash.
- Redis/BullMQ or RabbitMQ: reliable, but a second system, and still the "commit succeeded but enqueue failed" gap.
- **Outbox table in Postgres.**

**Choice.** The request inserts a `jobs` row **in the same transaction** as the change. A separate worker process:

- takes one due job with `FOR UPDATE SKIP LOCKED` (several workers can run safely without taking the same job);
- runs the handler inside a savepoint;
- on failure, increments `attempts` and retries with exponential backoff (2s, 4s, 8s… capped at 5 min);
- after `max_attempts` (5), marks the job `failed` and keeps `last_error` for investigation.

Delivery is **at-least-once**, so handlers are **idempotent**: notifications have `UNIQUE (job_id, user_id)` and use `ON CONFLICT DO NOTHING`. Running a job twice produces one notification (tested).

**Trade-off.** Polling a table adds a small delay (≤1s) and some database load. At much higher volume I'd move to `LISTEN/NOTIFY` or a dedicated queue fed from the outbox.

---

## Decision 6: Idempotency keys for "create"

**Context.** "A user repeating an action because they are unsure whether the first request succeeded."

**Choice.** The *New item* form generates a UUID when it opens and sends it as `Idempotency-Key`. The server stores `(user_id, key) → response` in the same transaction as the create:

- The first request inserts the key, creates the item, and saves the response.
- A repeat with the same key gets the **stored response** (header `Idempotent-Replayed: true`).
- If two copies arrive at the same instant, the primary key makes the second `INSERT … ON CONFLICT` wait for the first to commit, then replay its result. One item is created (tested with 5 parallel requests).
- The same key with a *different* body → `422` (a client bug that should be loud).
- If the request fails, the key rolls back with it, so the user can fix the input and retry.

**Why only on create.** Create is the one action that isn't naturally idempotent. Claim is idempotent by design (claiming your own item again returns success), and edits/transitions are protected by the version check: a repeated edit with the old version is rejected rather than applied twice.

**Trade-off.** Keys accumulate. They need a cleanup job (listed in Known Limitations).

---

## Decision 7: Built for the stated scale without extra infrastructure

**Context.** Tens of thousands of active items, a growing history, and hundreds to thousands of concurrent users.

**Choice.**
- **Server-side filtering and search only.** The browser never holds more than the pages it has loaded.
- **Keyset (cursor) pagination** on `(updated_at, id)` instead of `OFFSET`. Page 500 is as fast as page 1, and new items don't cause duplicates or skips.
- **Indexes match the actual queries:** `(team_id, status, updated_at, id)`, `(assignee_id, status)`, a *partial* index for "unassigned urgent", a GIN index for full-text search, and `(item_id, id)` for history.
- **Full-text search** with a generated `tsvector` column (Postgres keeps it up to date).
- **Activity history is paginated** too, because a busy item can have hundreds of events.
- The seed creates ~5,000 items so this is visible in the demo.

**What I'd change at 10× scale.**
- Read replicas for the list/dashboard queries.
- Partition `activity_events` by month, and archive closed items.
- A search engine (OpenSearch/Meilisearch) fed from the outbox, if search needs fuzziness or facets.
- A dedicated queue for jobs, websockets/SSE for live updates, and caching of dashboard counts.

---

## What I deliberately did NOT build

| Not built | Why |
|---|---|
| Real-time websockets | Polling + refetch-on-focus is enough to show fresh data; the effort went into correctness instead |
| Email/Slack delivery | The outbox handler is the integration point; delivery itself adds no engineering insight here |
| Sign-up, password reset, SSO | Seeded users are enough to demonstrate authorization; a company would use SSO anyway |
| Attachments, rich text, mobile app | Outside the core problem |
| Admin UI for teams/roles | Roles are seeded; the authorization model is the interesting part, not the CRUD screens for it |

The brief says *"a smaller system with well-considered behaviour"* beats many half-done features, and I scoped the work with that in mind.
