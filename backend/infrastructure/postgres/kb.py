import re
from datetime import datetime, timezone
from typing import Any

from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

from backend.core.errors import Invalid

MAX_RESULTS = 3
EXCERPT_CHARS = 400
SEARCHABLE_TOKEN = r"[A-Za-z0-9]+"

ARTICLES = [
    {
        "id": "kb_dark_mode",
        "title": "Dark mode and appearance settings",
        "body": (
            "The app has two appearance options under Settings > Appearance: Light and System Default. There is no "
            "separate dark toggle. Choose Settings > Appearance > System Default and the app follows the operating "
            "system: when the device is set to dark mode, the app is shown in the dark theme."
        ),
        "updated_at": datetime(2026, 3, 18, tzinfo=timezone.utc),
    },
    {
        "id": "kb_upgrade_payment",
        "title": "Upgrading to Pro and failed payments",
        "body": (
            "An upgrade to Pro takes effect when the payment is captured. A payment attempt that fails can leave a "
            "pending authorization hold on the card; a hold is not a charge and the bank releases it within a few "
            "days. If an upgrade failed and a charge was still captured, contact support: the billing team reviews "
            "the payment attempts on the account."
        ),
        "updated_at": datetime(2026, 5, 4, tzinfo=timezone.utc),
    },
    {
        "id": "kb_refunds",
        "title": "Refunds and duplicate charges",
        "body": (
            "A captured charge can be refunded in full by the billing team after review. Duplicate charges for one "
            "purchase are refunded once confirmed. A refund reaches the card in five to ten business days. Support "
            "cannot promise a refund before the billing team has reviewed the charge."
        ),
        "updated_at": datetime(2026, 2, 9, tzinfo=timezone.utc),
    },
    {
        "id": "kb_status_page",
        "title": "Service status and reporting an outage",
        "body": (
            "The public status page shows each component and region and any incident that has been acknowledged. "
            "The page is updated by the on-call team, so a new problem can appear there with a delay. If you see "
            "errors such as error 500 while the status page says operational, report it to support with the time, "
            "your region and whether colleagues are affected."
        ),
        "updated_at": datetime(2026, 1, 22, tzinfo=timezone.utc),
    },
    {
        "id": "kb_pro_export",
        "title": "Export features on the Pro plan",
        "body": (
            "The Pro plan adds export to PDF and slides and scheduled exports. Export features become available as "
            "soon as the account shows the Pro plan. On the Free plan the export menu shows the Pro options locked."
        ),
        "updated_at": datetime(2026, 4, 30, tzinfo=timezone.utc),
    },
]


class NoUsableToken(Invalid):
    code = "no-usable-token"
    title = "No usable search token"


def seed_articles(pool: ConnectionPool) -> None:
    with pool.connection() as conn:
        for article in ARTICLES:
            conn.execute(
                """
                INSERT INTO kb_articles (id, title, body, updated_at)
                VALUES (%(id)s, %(title)s, %(body)s, %(updated_at)s) ON CONFLICT DO NOTHING
                """,
                article,
            )


def search(pool: ConnectionPool, query: str) -> list[dict[str, Any]]:
    # Step 1: Keep the alphanumeric tokens and join them with OR, so one matching word is enough
    tokens = re.findall(SEARCHABLE_TOKEN, query)
    if not tokens:
        raise NoUsableToken
    terms = " | ".join(tokens)
    with pool.connection() as conn:
        # Step 2: Refuse a query made of stop words only, which parses to nothing
        if conn.execute("SELECT numnode(to_tsquery('english', %s))", (terms,)).fetchone()[0] == 0:
            raise NoUsableToken
        # Step 3: Return the best-matching articles, highest rank first
        return conn.cursor(row_factory=dict_row).execute(
            """
            SELECT id, title, left(body, %(chars)s) AS excerpt, updated_at,
                   round(ts_rank(search, query)::numeric, 4)::float8 AS score
              FROM kb_articles, to_tsquery('english', %(terms)s) AS query
             WHERE search @@ query
             ORDER BY score DESC, id
             LIMIT %(limit)s
            """,
            {"terms": terms, "chars": EXCERPT_CHARS, "limit": MAX_RESULTS},
        ).fetchall()
