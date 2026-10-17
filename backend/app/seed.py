"""Seed demo data: 4 teams, 10 users with different roles, and a few thousand
work items (enough to show that pagination, filters and search stay fast).

Run with:  python -m app.seed
It does nothing if users already exist, so it is safe to run on every start.
"""
import random
from datetime import datetime, timedelta, timezone

import psycopg
from psycopg.types.json import Jsonb

from . import config
from .security import hash_password

DEMO_PASSWORD = "password123"

TEAMS = ["Engineering", "Support", "Finance", "Compliance"]

# (email, name, {team: role})
USERS = [
    ("alice@opsdesk.dev", "Alice Lead",    {"Engineering": "lead", "Support": "member"}),
    ("bob@opsdesk.dev",   "Bob Member",    {"Engineering": "member"}),
    ("carol@opsdesk.dev", "Carol Member",  {"Engineering": "member"}),
    ("dave@opsdesk.dev",  "Dave Lead",     {"Support": "lead"}),
    ("erin@opsdesk.dev",  "Erin Member",   {"Support": "member"}),
    ("frank@opsdesk.dev", "Frank Lead",    {"Finance": "lead", "Compliance": "member"}),
    ("grace@opsdesk.dev", "Grace Member",  {"Finance": "member"}),
    ("heidi@opsdesk.dev", "Heidi Lead",    {"Compliance": "lead"}),
    ("judy@opsdesk.dev",  "Judy Member",   {"Compliance": "member"}),
    ("ivan@opsdesk.dev",  "Ivan Viewer",   {"Engineering": "viewer", "Support": "viewer",
                                            "Finance": "viewer", "Compliance": "viewer"}),
]

TITLES = {
    "incident": ["API latency spike on {svc}", "{svc} returning 500 errors", "Disk almost full on {svc} host",
                 "Login failures reported for {svc}", "Queue backlog growing in {svc}"],
    "customer_issue": ["Customer cannot reset password", "Refund not visible for order {n}",
                       "Invoice shows wrong address", "App crashes on checkout for customer {n}",
                       "Enterprise client asks for data export"],
    "payment": ["Payment {n} stuck in pending", "Duplicate charge on order {n}", "Chargeback received for {n}",
                "Settlement mismatch for batch {n}", "Failed payout to vendor {n}"],
    "compliance": ["GDPR deletion request {n}", "KYC document review for account {n}",
                   "Audit evidence for access review", "Data retention check for {svc}", "Vendor risk review {n}"],
    "task": ["Rotate credentials for {svc}", "Update runbook for {svc}", "Approve new vendor contract {n}",
             "Quarterly access review", "Clean up old feature flags in {svc}"],
}
TEAM_TYPES = {
    "Engineering": ["incident", "task", "customer_issue"],
    "Support": ["customer_issue", "task", "payment"],
    "Finance": ["payment", "task", "compliance"],
    "Compliance": ["compliance", "task"],
}
SERVICES = ["billing", "auth", "search", "checkout", "notifications", "reports", "payments-gateway"]
PRIORITIES = ["low", "medium", "medium", "high", "high", "urgent"]
STATUSES = ["open", "open", "in_progress", "in_progress", "awaiting_approval", "resolved", "closed"]


def seed(url: str | None = None, n_items: int | None = None) -> None:
    n_items = config.SEED_ITEMS if n_items is None else n_items
    rnd = random.Random(42)
    with psycopg.connect(url or config.DATABASE_URL) as conn:
        if conn.execute("SELECT count(*) FROM users").fetchone()[0] > 0:
            print("Seed skipped: data already present.")
            return

        team_ids = {}
        for name in TEAMS:
            team_ids[name] = conn.execute("INSERT INTO teams (name) VALUES (%s) RETURNING id", (name,)).fetchone()[0]

        pw = hash_password(DEMO_PASSWORD)
        user_ids, members_by_team = {}, {t: [] for t in TEAMS}
        for email, name, roles in USERS:
            uid = conn.execute("INSERT INTO users (email, name, password_hash) VALUES (%s, %s, %s) RETURNING id",
                               (email, name, pw)).fetchone()[0]
            user_ids[email] = uid
            for team, role in roles.items():
                conn.execute("INSERT INTO memberships (user_id, team_id, role) VALUES (%s, %s, %s)",
                             (uid, team_ids[team], role))
                if role in ("member", "lead"):
                    members_by_team[team].append(uid)

        now = datetime.now(timezone.utc)
        rows = []
        for _ in range(n_items):
            team = rnd.choice(TEAMS)
            typ = rnd.choice(TEAM_TYPES[team])
            title = rnd.choice(TITLES[typ]).format(svc=rnd.choice(SERVICES), n=rnd.randint(10000, 99999))
            status = rnd.choice(STATUSES)
            assignee = None if status == "open" and rnd.random() < 0.7 else rnd.choice(members_by_team[team])
            created = now - timedelta(days=rnd.uniform(0, 120))
            updated = created + (now - created) * rnd.random()
            due = (created + timedelta(days=rnd.randint(1, 30))).date() if rnd.random() < 0.5 else None
            rows.append((team_ids[team], title, f"Auto-generated demo item about: {title.lower()}.", typ,
                         rnd.choice(PRIORITIES), status, assignee, rnd.choice(members_by_team[team]),
                         due, created, updated))

        with conn.cursor() as cur:
            with cur.copy("COPY work_items (team_id, title, description, type, priority, status, assignee_id, "
                          "created_by, due_date, created_at, updated_at) FROM STDIN") as copy:
                for r in rows:
                    copy.write_row(r)
            # One "created" history event per seeded item.
            cur.execute("""
                INSERT INTO activity_events (item_id, actor_id, kind, data, created_at)
                SELECT id, created_by, 'created', jsonb_build_object('title', title, 'priority', priority), created_at
                FROM work_items
            """)

        # A few hand-written items that make the demo easy to follow.
        eng = team_ids["Engineering"]
        demo = [
            ("DEMO: Checkout returns 500 for some users", "urgent", "open", None),
            ("DEMO: Two people should not fix the same outage", "high", "open", None),
            ("DEMO: Hotfix for auth token expiry", "high", "awaiting_approval", user_ids["bob@opsdesk.dev"]),
        ]
        for title, prio, status, assignee in demo:
            item_id = conn.execute(
                "INSERT INTO work_items (team_id, title, description, type, priority, status, assignee_id, created_by)"
                " VALUES (%s, %s, %s, 'incident', %s, %s, %s, %s) RETURNING id",
                (eng, title, "Use this item to try the demo scenarios in the README.", prio, status, assignee,
                 user_ids["alice@opsdesk.dev"]),
            ).fetchone()[0]
            conn.execute("INSERT INTO activity_events (item_id, actor_id, kind, data) VALUES (%s, %s, 'created', %s)",
                         (item_id, user_ids["alice@opsdesk.dev"], Jsonb({"title": title, "priority": prio})))

        conn.execute("ANALYZE")
        print(f"Seeded {len(TEAMS)} teams, {len(USERS)} users, {n_items + len(demo)} work items. "
              f"Password for every user: {DEMO_PASSWORD}")


if __name__ == "__main__":
    seed()
