# OpsDesk: Operations Under Pressure

OpsDesk is an internal web app for coordinating operational work across teams: incidents, customer issues, payment investigations, compliance requests, and tasks that need approval.

Every item has a clear owner, status, priority and a full history. Users get a dashboard of what needs their attention. The system behaves correctly when many people work on the same item at the same moment.

**Stack:** Python (FastAPI) · PostgreSQL · React + TanStack Query · Postgres-backed job worker · Pytest · Docker Compose

---

## 1. Run it

**Prerequisites:** Docker Desktop (or Docker Engine + Compose v2). Nothing else.

```bash
git clone <this-repo-url> newtonite-ops
cd newtonite-ops
docker compose up --build
```

Open **http://localhost:8000**.

The first start builds the images, creates the schema, and seeds **4 teams, 10 users and ~5,000 work items** (so pagination and search have real data). After that, it starts in a few seconds.

To start again from an empty database:

```bash
docker compose down -v && docker compose up --build
```

---

## 2. Demo logins

The password for every user is **`password123`**.

| Email | Role(s) | Good for trying |
|---|---|---|
| `alice@opsdesk.dev` | **Lead** of Engineering, Member of Support | Approving, assigning, notifications |
| `bob@opsdesk.dev` | Member of Engineering | Claiming, doing work, submitting for approval |
| `carol@opsdesk.dev` | Member of Engineering | Racing Bob for the same item |
| `ivan@opsdesk.dev` | **Viewer** of all teams | Read-only access |
| `dave@opsdesk.dev` | Lead of Support | A different team (cannot see Engineering-only data) |
| `erin@`, `frank@`, `grace@`, `heidi@`, `judy@opsdesk.dev` | Other teams | |

---

## 3. Try the critical behaviours (2-minute demo)

Open two browser windows side by side (e.g. one normal, one private): **Bob** in one and **Carol** in the other.

1. **Two people claim the same item.** Both open *Unassigned urgent* and click **Claim** on `DEMO: Two people should not fix the same outage` at about the same time. One wins. The other sees their optimistic "claimed" state roll back with *"Already claimed by …"*.
2. **Stale edit.** Alice opens `DEMO: Checkout returns 500…` and clicks **Edit**. Meanwhile Bob changes its priority. When Alice clicks **Save** she gets *"This item was changed by someone else"* and her stale change is **not** applied.
3. **Workflow and approval.** Bob claims an item, clicks **Start work**, then **Submit for approval**. Bob has no *Approve* button, and the API refuses him too. Alice gets a notification (made by the background worker) and approves.
4. **Authorization.** Log in as Ivan (viewer): he can read everything but has no action buttons, and the API returns 403 if he tries. Dave (Support) can't open Engineering items at all.
5. **Duplicate requests.** The *New item* form sends an `Idempotency-Key`. Double-clicking *Create*, or retrying after a timeout, creates exactly one item.

---

## 4. Run the tests

Tests run against a **real PostgreSQL** (row locks and constraints are the point; mocks would hide bugs).

```bash
docker compose up -d db
docker compose run --rm app pytest -v
```

**What is covered** (29 tests, in `backend/tests/`):

| File | Behaviour |
|---|---|
| `test_concurrency.py` | 8 users claim at once → exactly one wins; stale version rejected and data unchanged; 5 simultaneous edits → exactly one applied |
| `test_idempotency.py` | Same key twice → one item, same response; same key fired concurrently → one item; key reused for a different body → 422; keys are per-user; a failed request doesn't burn the key |
| `test_workflow.py` | Open → Closed rejected; can't start without an owner; member can't approve, lead can; closed is final; every change appears in history; rejected changes write no history |
| `test_authorization.py` | No token → 401; other team → 403 on read, edit, claim, comment, list; viewer is read-only; only leads assign; can't assign outside the team |
| `test_worker.py` | Job written in the same transaction as the change; no job if the change fails; job run twice → one notification; failing job retries with backoff then becomes `failed` |

---

## 5. Run without Docker (for development)

You need Python 3.12+, Node 20+ and a local PostgreSQL 16.

```bash
# Backend
cd backend
python -m venv .venv && source .venv/bin/activate      # Windows: .venv\Scripts\activate
pip install -r requirements.txt
export DATABASE_URL=postgresql://postgres:postgres@localhost:5432/opsdesk
python -m app.migrate && python -m app.seed
uvicorn app.main:app --reload                           # API on :8000
python -m app.worker                                    # in a second terminal

# Frontend (third terminal)
cd frontend
npm install
npm run dev                                             # UI on http://localhost:5173

# Tests (needs an empty database called opsdesk_test)
cd backend
TEST_DATABASE_URL=postgresql://postgres:postgres@localhost:5432/opsdesk_test pytest -v
```

---

## 6. Architecture

```mermaid
flowchart LR
    B["Browser<br/>React + TanStack Query"] -- "HTTPS / JSON<br/>Bearer token" --> A["API<br/>FastAPI"]
    A -- "one transaction per request:<br/>item + activity + job" --> P[("PostgreSQL")]
    W["Worker<br/>python -m app.worker"] -- "SELECT … FOR UPDATE SKIP LOCKED<br/>retry with backoff" --> P
    W -- "writes" --> N["notifications table"]
    B -- "polls every 10–30s" --> A
```

**One request, step by step** (e.g. *assign item to Bob*):

1. The API checks the token and loads the user's team roles from the database.
2. It opens **one transaction**, then `SELECT … FOR UPDATE` locks the item row.
3. Permission check (is the caller a lead of this item's team?), version check (is the client's copy current?), and rule check.
4. `UPDATE` the item (`version + 1`), `INSERT` an activity event, and `INSERT` a `notify_assignee` job.
5. Commit. All of it is saved, or none of it is.
6. Later, the worker picks up the job and writes Bob's notification.

**Code layout**

```
backend/app/
  main.py            HTTP routes, input validation, error format (thin)
  items_service.py   all work-item business logic (the important file)
  permissions.py     who may do what (one place, with a matrix)
  workflow.py        the state machine
  idempotency.py     Idempotency-Key handling
  outbox.py          job enqueue + idempotent job handlers
  worker.py          background job runner (retries, backoff, max attempts)
  security.py        passwords, tokens, current user
  schema.sql         tables, constraints, indexes
  seed.py            demo data
backend/tests/       pytest suite (real Postgres)
frontend/src/
  pages/             Login, Dashboard, ItemDetail
  hooks.js           optimistic claim with rollback
  api.js             fetch wrapper + error type
```

**Main API endpoints**

| Method & path | Purpose |
|---|---|
| `POST /api/auth/login` | Email + password → token |
| `GET /api/me` | Current user and team roles |
| `GET /api/items?view=&status=&priority=&type=&team_id=&assignee=&q=&cursor=` | Filtered, searchable, cursor-paginated list |
| `GET /api/dashboard` | Counts for the three "needs attention" views |
| `POST /api/items` (header `Idempotency-Key`) | Create item |
| `GET /api/items/:id` | Item detail (+ allowed next statuses) |
| `PATCH /api/items/:id` `{version, …fields}` | Edit, with optimistic locking |
| `POST /api/items/:id/claim` | Atomic claim |
| `POST /api/items/:id/release` | Give an item back |
| `POST /api/items/:id/assign` `{assignee_id, version}` | Lead assigns |
| `POST /api/items/:id/transition` `{to_status, version}` | Workflow step |
| `GET /api/items/:id/activity` | History timeline (paginated) |
| `POST /api/items/:id/comments` | Comment |
| `GET /api/notifications` | Notifications created by the worker |

All errors have one shape: `{"error": {"code": "version_conflict", "message": "...", "current": {...}}}`.

---

## 7. Known limitations

- **No real-time push.** Screens poll every 10–30 seconds and refetch on window focus. Websockets/SSE would make updates instant.
- **Notifications are in-app only.** No email or Slack delivery yet, though the outbox makes adding one easy.
- **Team roles are seeded.** There is no admin screen for managing users, teams or roles, and no sign-up or password reset.
- **Items can't move between teams**, and there is no item deletion. Closing is the end of the life cycle, and history is never deleted.
- **List ordering is by "last updated" only.** Cursor pagination on `(updated_at, id)` is stable, but if an item is updated while you page, it can move to the top and you won't see it on later pages until you refresh.
- **Search is Postgres full-text** (English stemming, title + description). It works well at this size but has no fuzzy matching or typo tolerance.
- **Token stored in localStorage** with a 12-hour expiry and no refresh/revocation list. An HttpOnly cookie plus CSRF protection would be safer.
- **Schema "migrations"** are one idempotent `schema.sql`. A real project would use versioned migrations (e.g. Alembic).
- **Idempotency keys are never cleaned up.** A daily job should delete keys older than ~24 hours.
- **Frontend has no automated tests.** The risky logic is server-side and covered there; the optimistic rollback was verified manually and with a Playwright script during development.

---

## 8. With another week, I would…

1. Add Server-Sent Events so lists and item pages update instantly when someone else changes something.
2. Add an admin screen for teams and roles, with every permission change recorded in the audit log.
3. Add SLA tracking: due-date breach warnings, and an "overdue" view sorted by risk.
4. Use versioned migrations (Alembic), add structured logging, add metrics (request latency, job queue depth, failed jobs), and add a "failed jobs" admin page with a retry button.
5. Add frontend tests (Playwright) for the optimistic rollback and the conflict banner.

See **[ENGINEERING_DECISIONS.md](ENGINEERING_DECISIONS.md)** for the reasoning behind the design.
