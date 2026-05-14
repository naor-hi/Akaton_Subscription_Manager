"""
db_manager.py
-------------
Data Access Layer (DAL) for the Autonomous Personal Finance Agent.

Responsibilities:
  - Initialize the local SQLite database and schema.
  - Provide idempotent upsert operations for daily usage telemetry.
  - Expose CRUD helpers for subscription records.

All queries use parameterized statements to prevent SQL injection.
"""

import sqlite3
import logging
from contextlib import contextmanager
from datetime import date
from typing import Optional

# ---------------------------------------------------------------------------
# Logging configuration
# ---------------------------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s – %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# DDL statements
# ---------------------------------------------------------------------------
_DDL_USER_SUBSCRIPTIONS = """
CREATE TABLE IF NOT EXISTS user_subscriptions (
    subscription_id   TEXT        PRIMARY KEY,
    service_name      TEXT        NOT NULL,
    monthly_cost      REAL        NOT NULL,
    currency          TEXT        NOT NULL DEFAULT 'USD',
    unsubscribe_url   TEXT,
    last_updated      TIMESTAMP   NOT NULL DEFAULT CURRENT_TIMESTAMP
);
"""

_DDL_DAILY_USAGE_LOG = """
CREATE TABLE IF NOT EXISTS daily_usage_log (
    log_id                   INTEGER  PRIMARY KEY AUTOINCREMENT,
    subscription_id          TEXT     NOT NULL
                                      REFERENCES user_subscriptions(subscription_id)
                                      ON DELETE CASCADE,
    log_date                 DATE     NOT NULL,
    device_type              TEXT     NOT NULL,
    active_duration_minutes  INTEGER  NOT NULL DEFAULT 0,

    -- Idempotency: one row per (subscription × day × device)
    CONSTRAINT uq_usage_per_day_device
        UNIQUE (subscription_id, log_date, device_type)
);
"""

# ---------------------------------------------------------------------------
# Connection helper
# ---------------------------------------------------------------------------
@contextmanager
def _get_connection(db_path: str):
    """
    Yield a sqlite3 connection with foreign-key enforcement enabled.
    Commits on clean exit; rolls back and re-raises on any exception.
    """
    conn = sqlite3.connect(db_path, detect_types=sqlite3.PARSE_DECLTYPES)
    conn.row_factory = sqlite3.Row          # access columns by name
    conn.execute("PRAGMA foreign_keys = ON;")
    try:
        yield conn
        conn.commit()
    except sqlite3.Error as exc:
        conn.rollback()
        logger.error("Database error – transaction rolled back: %s", exc)
        raise
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Schema initialisation
# ---------------------------------------------------------------------------
def initialize_db(db_path: str = "agent.db") -> None:
    """
    Create the database file (if absent) and apply the schema.

    Tables created:
      • user_subscriptions  – one row per tracked service.
      • daily_usage_log     – device-level usage telemetry, one row per
                              (subscription_id, log_date, device_type).

    Safe to call multiple times; uses CREATE TABLE IF NOT EXISTS.

    Args:
        db_path: File-system path for the SQLite database.
                 Defaults to ``agent.db`` in the current working directory.
    """
    logger.info("Initialising database at '%s' …", db_path)
    with _get_connection(db_path) as conn:
        conn.execute(_DDL_USER_SUBSCRIPTIONS)
        conn.execute(_DDL_DAILY_USAGE_LOG)
    logger.info("Schema ready (user_subscriptions, daily_usage_log).")


# ---------------------------------------------------------------------------
# Subscription CRUD
# ---------------------------------------------------------------------------
def upsert_subscription(
    subscription_id: str,
    service_name: str,
    monthly_cost: float,
    currency: str = "USD",
    unsubscribe_url: Optional[str] = None,
    db_path: str = "agent.db",
) -> None:
    """
    Insert a new subscription or update an existing one (by subscription_id).

    Args:
        subscription_id: Stable unique identifier (e.g. ``"netflix_us"``).
        service_name:    Human-readable name (e.g. ``"Netflix"``).
        monthly_cost:    Recurring charge in *currency* units.
        currency:        ISO-4217 currency code.  Defaults to ``"USD"``.
        unsubscribe_url: Direct cancellation URL surfaced to the agent.
        db_path:         Path to the SQLite database file.
    """
    sql = """
        INSERT INTO user_subscriptions
            (subscription_id, service_name, monthly_cost, currency,
             unsubscribe_url, last_updated)
        VALUES (?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
        ON CONFLICT(subscription_id) DO UPDATE SET
            service_name    = excluded.service_name,
            monthly_cost    = excluded.monthly_cost,
            currency        = excluded.currency,
            unsubscribe_url = excluded.unsubscribe_url,
            last_updated    = CURRENT_TIMESTAMP;
    """
    params = (subscription_id, service_name, monthly_cost, currency, unsubscribe_url)
    logger.debug("Upserting subscription '%s' (%s).", subscription_id, service_name)
    with _get_connection(db_path) as conn:
        conn.execute(sql, params)
    logger.info("Subscription '%s' saved.", subscription_id)


def get_all_subscriptions(db_path: str = "agent.db") -> list[sqlite3.Row]:
    """
    Return every row from ``user_subscriptions`` ordered by service name.

    Args:
        db_path: Path to the SQLite database file.

    Returns:
        A list of :class:`sqlite3.Row` objects (accessible by column name).
    """
    sql = "SELECT * FROM user_subscriptions ORDER BY service_name;"
    with _get_connection(db_path) as conn:
        rows = conn.execute(sql).fetchall()
    logger.debug("Fetched %d subscription(s).", len(rows))
    return rows


# ---------------------------------------------------------------------------
# Daily usage log – idempotent upsert
# ---------------------------------------------------------------------------
def log_daily_usage(
    subscription_id: str,
    log_date: date,
    device_type: str,
    duration_minutes: int,
    db_path: str = "agent.db",
) -> None:
    """
    Record (or update) device usage for a subscription on a given day.

    Uses ``ON CONFLICT DO UPDATE`` so that re-processing the same telemetry
    event is safe – the row is simply overwritten with the latest value.

    The UNIQUE constraint on ``(subscription_id, log_date, device_type)``
    guarantees at-most-one row per combination, enabling idempotent ingestion
    from email-receipt pipelines or device-telemetry feeds.

    Args:
        subscription_id:   Must match an existing row in ``user_subscriptions``.
        log_date:          Calendar date of the usage event.
        device_type:       E.g. ``"mobile"``, ``"desktop"``, ``"smart_tv"``.
        duration_minutes:  Total active minutes for that day on that device.
        db_path:           Path to the SQLite database file.

    Raises:
        sqlite3.IntegrityError: If *subscription_id* does not exist in
            ``user_subscriptions`` (foreign-key violation).
        sqlite3.Error:          On any other database-level failure.
    """
    if duration_minutes < 0:
        raise ValueError(
            f"duration_minutes must be non-negative, got {duration_minutes}."
        )

    sql = """
        INSERT INTO daily_usage_log
            (subscription_id, log_date, device_type, active_duration_minutes)
        VALUES (?, ?, ?, ?)
        ON CONFLICT(subscription_id, log_date, device_type) DO UPDATE SET
            active_duration_minutes = excluded.active_duration_minutes;
    """
    params = (subscription_id, log_date.isoformat(), device_type, duration_minutes)
    logger.debug(
        "Logging usage – sub='%s', date=%s, device='%s', mins=%d.",
        subscription_id, log_date, device_type, duration_minutes,
    )
    with _get_connection(db_path) as conn:
        conn.execute(sql, params)
    logger.info(
        "Usage logged: '%s' on %s via %s → %d min.",
        subscription_id, log_date, device_type, duration_minutes,
    )


# ---------------------------------------------------------------------------
# ROI / analytics helpers
# ---------------------------------------------------------------------------
def get_usage_summary(
    db_path: str = "agent.db",
) -> list[sqlite3.Row]:
    """
    Return per-subscription usage totals joined with cost data.

    Columns returned:
      subscription_id, service_name, monthly_cost, currency,
      unsubscribe_url, total_minutes_this_month, cost_per_minute

    ``cost_per_minute`` is NULL when total usage is zero (division guard).

    Args:
        db_path: Path to the SQLite database file.

    Returns:
        List of :class:`sqlite3.Row` objects, ordered by cost_per_minute DESC
        (most expensive-per-minute first – prime cancellation candidates).
    """
    sql = """
        SELECT
            s.subscription_id,
            s.service_name,
            s.monthly_cost,
            s.currency,
            s.unsubscribe_url,
            COALESCE(SUM(u.active_duration_minutes), 0)          AS total_minutes_this_month,
            CASE
                WHEN COALESCE(SUM(u.active_duration_minutes), 0) = 0 THEN NULL
                ELSE ROUND(s.monthly_cost /
                           SUM(u.active_duration_minutes), 6)
            END                                                   AS cost_per_minute
        FROM user_subscriptions s
        LEFT JOIN daily_usage_log u
               ON u.subscription_id = s.subscription_id
              AND strftime('%Y-%m', u.log_date) =
                  strftime('%Y-%m', 'now')
        GROUP BY s.subscription_id
        ORDER BY cost_per_minute DESC NULLS FIRST;
    """
    with _get_connection(db_path) as conn:
        rows = conn.execute(sql).fetchall()
    logger.debug("Usage summary fetched for %d subscription(s).", len(rows))
    return rows


# ---------------------------------------------------------------------------
# Smoke-test / quick demo  (python db_manager.py)
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    import os
    from datetime import date, timedelta

    DB = "agent_demo.db"

    # Clean slate for the demo
    if os.path.exists(DB):
        os.remove(DB)

    initialize_db(DB)

    # Seed subscriptions
    upsert_subscription("netflix_us",  "Netflix",  15.99, "USD",
                        "https://www.netflix.com/cancelplan", DB)
    upsert_subscription("spotify_us",  "Spotify",   9.99, "USD",
                        "https://www.spotify.com/account/subscription/cancel", DB)
    upsert_subscription("hulu_us",     "Hulu",      7.99, "USD",
                        "https://secure.hulu.com/account/cancel", DB)

    # Simulate 7 days of usage (Netflix heavy, Spotify light, Hulu zero)
    today = date.today()
    for i in range(7):
        day = today - timedelta(days=i)
        log_daily_usage("netflix_us", day, "smart_tv",  90, DB)
        log_daily_usage("netflix_us", day, "mobile",    30, DB)
        log_daily_usage("spotify_us", day, "mobile",     5, DB)
        # Idempotency check – re-logging same event should not duplicate
        log_daily_usage("netflix_us", day, "smart_tv",  90, DB)

    # Print ROI summary
    print("\n── ROI Summary ──────────────────────────────────────────────")
    print(f"{'Service':<14} {'Cost/mo':>8}  {'Mins':>6}  {'$/min':>9}  Unsubscribe URL")
    print("─" * 80)
    for row in get_usage_summary(DB):
        cost_per_min = f"{row['cost_per_minute']:.6f}" if row['cost_per_minute'] else "  N/A   "
        print(
            f"{row['service_name']:<14} "
            f"{row['monthly_cost']:>7.2f}  "
            f"{row['total_minutes_this_month']:>6}  "
            f"{cost_per_min:>9}  "
            f"{row['unsubscribe_url'] or '—'}"
        )
    print("─" * 80)
    print("Demo complete. Database saved to:", DB)
