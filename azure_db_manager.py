"""
azure_db_manager.py
-------------------
Data Access Layer (DAL) for the Autonomous Personal Finance Agent.
Cloud backend: Azure SQL Database (via pyodbc + ODBC Driver 18).

Drop-in replacement for db_manager.py – identical public API, same table
schema, same idempotent upsert semantics.

Configuration is read exclusively from environment variables (or a .env file)
so that no credentials ever appear in source code.

Required env vars (see .env):
    AZURE_SQL_SERVER    – e.g. myserver.database.windows.net
    AZURE_SQL_DATABASE  – e.g. subscription_manager
    AZURE_SQL_USERNAME  – SQL admin login
    AZURE_SQL_PASSWORD  – SQL admin password
    AZURE_SQL_DRIVER    – ODBC driver name, e.g. "ODBC Driver 18 for SQL Server"
"""

import logging
import os
from contextlib import contextmanager
from datetime import date
from typing import Optional

import pyodbc
from dotenv import load_dotenv

# ---------------------------------------------------------------------------
# Bootstrap
# ---------------------------------------------------------------------------
load_dotenv()  # reads .env into os.environ (no-op if already set)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s – %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Connection string builder
# ---------------------------------------------------------------------------
def _build_connection_string() -> str:
    """
    Assemble the pyodbc connection string from environment variables.
    Raises EnvironmentError if any required variable is missing.
    """
    required = {
        "AZURE_SQL_SERVER":   os.getenv("AZURE_SQL_SERVER"),
        "AZURE_SQL_DATABASE": os.getenv("AZURE_SQL_DATABASE"),
        "AZURE_SQL_USERNAME": os.getenv("AZURE_SQL_USERNAME"),
        "AZURE_SQL_PASSWORD": os.getenv("AZURE_SQL_PASSWORD"),
        "AZURE_SQL_DRIVER":   os.getenv("AZURE_SQL_DRIVER",
                                        "ODBC Driver 18 for SQL Server"),
    }
    missing = [k for k, v in required.items() if not v]
    if missing:
        raise EnvironmentError(
            f"Missing required environment variable(s): {', '.join(missing)}. "
            "Check your .env file or Azure App Service configuration."
        )

    return (
        f"DRIVER={{{required['AZURE_SQL_DRIVER']}}};"
        f"SERVER={required['AZURE_SQL_SERVER']};"
        f"DATABASE={required['AZURE_SQL_DATABASE']};"
        f"UID={required['AZURE_SQL_USERNAME']};"
        f"PWD={required['AZURE_SQL_PASSWORD']};"
        "Encrypt=yes;"
        "TrustServerCertificate=no;"
        "Connection Timeout=30;"
    )


# ---------------------------------------------------------------------------
# DDL – T-SQL syntax (Azure SQL / SQL Server)
# ---------------------------------------------------------------------------
_DDL_USER_SUBSCRIPTIONS = """
IF NOT EXISTS (
    SELECT 1 FROM sys.tables WHERE name = 'user_subscriptions'
)
CREATE TABLE user_subscriptions (
    subscription_id             NVARCHAR(128)   NOT NULL PRIMARY KEY,
    service_name                NVARCHAR(256)   NOT NULL,
    category                    NVARCHAR(128)   NULL,
    website_url                 NVARCHAR(2048)  NULL,
    unsubscribe_url             NVARCHAR(2048)  NULL,
    monthly_cost                DECIMAL(10, 2)  NOT NULL,
    currency                    NCHAR(3)        NOT NULL DEFAULT 'USD',
    billing_cycle               NVARCHAR(32)    NOT NULL DEFAULT 'monthly',
    next_billing_date           DATE            NULL,
    current_period_usage_hours  DECIMAL(8, 2)   NOT NULL DEFAULT 0,
    usage_threshold_hours       DECIMAL(8, 2)   NULL,
    agent_recommendation        NVARCHAR(16)    NULL,
    last_sync_timestamp         DATETIME2       NOT NULL DEFAULT SYSUTCDATETIME()
);
"""

_DDL_DAILY_USAGE_LOG = """
IF NOT EXISTS (
    SELECT 1 FROM sys.tables WHERE name = 'daily_usage_log'
)
CREATE TABLE daily_usage_log (
    log_id                   INT             NOT NULL IDENTITY(1,1) PRIMARY KEY,
    subscription_id          NVARCHAR(128)   NOT NULL
        REFERENCES user_subscriptions(subscription_id) ON DELETE CASCADE,
    log_date                 DATE            NOT NULL,
    device_type              NVARCHAR(64)    NOT NULL,
    active_duration_minutes  INT             NOT NULL DEFAULT 0,

    -- Idempotency: one row per (subscription × day × device)
    CONSTRAINT uq_usage_per_day_device
        UNIQUE (subscription_id, log_date, device_type)
);
"""


# ---------------------------------------------------------------------------
# Connection helper
# ---------------------------------------------------------------------------
@contextmanager
def _get_connection():
    """
    Yield a pyodbc connection with autocommit OFF.
    Commits on clean exit; rolls back and re-raises on any exception.
    """
    conn_str = _build_connection_string()
    conn = pyodbc.connect(conn_str, autocommit=False)
    try:
        yield conn
        conn.commit()
    except pyodbc.Error as exc:
        conn.rollback()
        logger.error("Azure SQL error – transaction rolled back: %s", exc)
        raise
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Schema initialisation
# ---------------------------------------------------------------------------
def initialize_db() -> None:
    """
    Create the Azure SQL tables if they don't already exist.

    Tables created:
      • user_subscriptions  – one row per tracked service.
      • daily_usage_log     – device-level usage telemetry, one row per
                              (subscription_id, log_date, device_type).

    Safe to call multiple times; uses IF NOT EXISTS guards.
    """
    logger.info(
        "Initialising Azure SQL database '%s' on server '%s' …",
        os.getenv("AZURE_SQL_DATABASE"),
        os.getenv("AZURE_SQL_SERVER"),
    )
    with _get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(_DDL_USER_SUBSCRIPTIONS)
        cursor.execute(_DDL_DAILY_USAGE_LOG)
    logger.info("Schema ready (user_subscriptions, daily_usage_log).")


# ---------------------------------------------------------------------------
# Subscription CRUD
# ---------------------------------------------------------------------------
def upsert_subscription(
    subscription_id: str,
    service_name: str,
    monthly_cost: float = 0.0,
    currency: str = "USD",
    category: Optional[str] = None,
    website_url: Optional[str] = None,
    unsubscribe_url: Optional[str] = None,
    billing_cycle: str = "monthly",
    next_billing_date: Optional[str] = None,
    current_period_usage_hours: float = 0.0,
    usage_threshold_hours: float = 0.0,
    agent_recommendation: Optional[str] = None,
    last_sync_timestamp: Optional[str] = None,
) -> None:
    """
    Insert a new subscription or update an existing one (by subscription_id).

    Uses T-SQL MERGE for an atomic upsert.

    Args:
        subscription_id:            Stable unique identifier (e.g. ``"netflix_us"``).
        service_name:               Human-readable name (e.g. ``"Netflix"``).
        monthly_cost:               Recurring charge in *currency* units. Defaults to 0.
        currency:                   ISO-4217 currency code. Defaults to ``"USD"``.
        category:                   e.g. ``"Streaming"``, ``"Productivity"``.
        website_url:                Main website URL.
        unsubscribe_url:            Direct cancellation URL.
        billing_cycle:              ``"Monthly"``, ``"Yearly"``, etc.
        next_billing_date:          Next charge date as ``"YYYY-MM-DD"`` string.
        current_period_usage_hours: Hours used in the current billing period.
        usage_threshold_hours:      Hours/month below which agent flags for review.
        agent_recommendation:       ``"Keep"``, ``"Consider_Canceling"``, ``"Unused"``, or None.
        last_sync_timestamp:        ISO-8601 datetime string. If None, DB sets SYSUTCDATETIME().
    """
    # Use caller-supplied timestamp if provided, otherwise let DB set it
    ts = last_sync_timestamp if last_sync_timestamp else None

    sql = """
        MERGE user_subscriptions AS target
        USING (VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?))
              AS source (subscription_id, service_name, category,
                         website_url, unsubscribe_url, monthly_cost,
                         currency, billing_cycle, next_billing_date,
                         current_period_usage_hours, usage_threshold_hours,
                         agent_recommendation, last_sync_timestamp)
        ON target.subscription_id = source.subscription_id
        WHEN MATCHED THEN
            UPDATE SET
                service_name                = source.service_name,
                category                    = source.category,
                website_url                 = source.website_url,
                unsubscribe_url             = source.unsubscribe_url,
                monthly_cost                = source.monthly_cost,
                currency                    = source.currency,
                billing_cycle               = source.billing_cycle,
                next_billing_date           = source.next_billing_date,
                current_period_usage_hours  = source.current_period_usage_hours,
                usage_threshold_hours       = source.usage_threshold_hours,
                agent_recommendation        = source.agent_recommendation,
                last_sync_timestamp         = COALESCE(source.last_sync_timestamp,
                                                        SYSUTCDATETIME())
        WHEN NOT MATCHED THEN
            INSERT (subscription_id, service_name, category,
                    website_url, unsubscribe_url, monthly_cost,
                    currency, billing_cycle, next_billing_date,
                    current_period_usage_hours, usage_threshold_hours,
                    agent_recommendation, last_sync_timestamp)
            VALUES (source.subscription_id, source.service_name, source.category,
                    source.website_url, source.unsubscribe_url, source.monthly_cost,
                    source.currency, source.billing_cycle, source.next_billing_date,
                    source.current_period_usage_hours, source.usage_threshold_hours,
                    source.agent_recommendation,
                    COALESCE(source.last_sync_timestamp, SYSUTCDATETIME()));
    """
    params = (
        subscription_id, service_name, category,
        website_url, unsubscribe_url, monthly_cost,
        currency, billing_cycle, next_billing_date,
        current_period_usage_hours, usage_threshold_hours,
        agent_recommendation, ts,
    )
    logger.debug("Upserting subscription '%s' (%s).", subscription_id, service_name)
    with _get_connection() as conn:
        conn.cursor().execute(sql, params)
    logger.info("Subscription '%s' saved.", subscription_id)


def get_all_subscriptions(
) -> list[dict]:
    """
    Return every row from ``user_subscriptions`` ordered by service name.

    Returns:
        A list of dicts with keys matching column names.
    """
    sql = "SELECT * FROM user_subscriptions ORDER BY service_name;"
    with _get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(sql)
        columns = [col[0] for col in cursor.description]
        rows = [dict(zip(columns, row)) for row in cursor.fetchall()]
    logger.debug("Fetched %d subscription(s).", len(rows))
    return rows


def get_raw_subscriptions() -> list[dict]:
    """
    Return all subscription rows in the RawSubscriptionData shape
    expected by the Analysis Engine client.

    Columns returned match the API contract exactly:
      subscription_id, service_name, category, website_url,
      unsubscribe_url, monthly_cost, currency, billing_cycle,
      next_billing_date, device_usage_hours (alias of current_period_usage_hours)
    """
    sql = """
        SELECT
            subscription_id,
            service_name,
            category,
            website_url,
            unsubscribe_url,
            monthly_cost,
            currency,
            billing_cycle,
            next_billing_date,
            current_period_usage_hours  AS device_usage_hours
        FROM user_subscriptions
        ORDER BY service_name;
    """
    with _get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(sql)
        columns = [col[0] for col in cursor.description]
        rows = [dict(zip(columns, row)) for row in cursor.fetchall()]
    logger.debug("get_raw_subscriptions: returned %d row(s).", len(rows))
    return rows


def update_analyzed_subscriptions(records: list[dict]) -> int:
    """
    Bulk-update the four analysis columns written by the Analysis Engine.

    Each dict in *records* must contain:
      subscription_id, current_period_usage_hours,
      usage_threshold_hours, agent_recommendation, last_sync_timestamp

    Uses a targeted UPDATE (not a full MERGE) so that the read-only fields
    (service_name, monthly_cost, etc.) are never touched.

    Args:
        records: List of AnalyzedSubscriptionData dicts from the engine.

    Returns:
        Number of rows successfully updated.
    """
    sql = """
        UPDATE user_subscriptions
        SET
            current_period_usage_hours = ?,
            usage_threshold_hours      = ?,
            agent_recommendation       = ?,
            last_sync_timestamp        = ?
        WHERE subscription_id = ?;
    """
    updated = 0
    with _get_connection() as conn:
        cursor = conn.cursor()
        for rec in records:
            cursor.execute(sql, (
                rec["current_period_usage_hours"],
                rec["usage_threshold_hours"],
                rec["agent_recommendation"],
                rec["last_sync_timestamp"],
                rec["subscription_id"],
            ))
            updated += cursor.rowcount
    logger.info("update_analyzed_subscriptions: updated %d row(s).", updated)
    return updated


def delete_subscription(subscription_id: str) -> bool:
    """
    Permanently delete a subscription and ALL its usage history.

    The FK ON DELETE CASCADE on ``daily_usage_log`` means all related
    usage rows are removed automatically in the same transaction.

    Args:
        subscription_id: The subscription to delete.

    Returns:
        True  – row was found and deleted.
        False – no row with that ID existed (nothing changed).
    """
    sql = "DELETE FROM user_subscriptions WHERE subscription_id = ?;"
    with _get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(sql, (subscription_id,))
        deleted = cursor.rowcount > 0
    if deleted:
        logger.info("Subscription '%s' deleted (usage history removed).", subscription_id)
    else:
        logger.warning("delete_subscription: '%s' not found.", subscription_id)
    return deleted


def delete_usage_log(
    subscription_id: str,
    log_date: Optional[date] = None,
    device_type: Optional[str] = None,
) -> int:
    """
    Delete usage log entries with flexible filtering.

    Combinations:
      • subscription_id only          → deletes ALL history for that service
      • subscription_id + log_date    → deletes all devices for that day
      • subscription_id + device_type → deletes all days for that device
      • all three                     → deletes exactly one row

    Args:
        subscription_id: Required – which subscription's logs to target.
        log_date:        Optional date filter.
        device_type:     Optional device filter.

    Returns:
        Number of rows deleted.
    """
    conditions = ["subscription_id = ?"]
    params: list = [subscription_id]

    if log_date is not None:
        conditions.append("log_date = ?")
        params.append(log_date.isoformat())
    if device_type is not None:
        conditions.append("device_type = ?")
        params.append(device_type)

    sql = f"DELETE FROM daily_usage_log WHERE {' AND '.join(conditions)};"
    with _get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(sql, params)
        deleted = cursor.rowcount
    logger.info(
        "Deleted %d usage log row(s) for '%s' (date=%s, device=%s).",
        deleted, subscription_id, log_date, device_type,
    )
    return deleted


# ---------------------------------------------------------------------------
# Daily usage log – idempotent upsert
# ---------------------------------------------------------------------------
def log_daily_usage(
    subscription_id: str,
    log_date: date,
    device_type: str,
    duration_minutes: int,
) -> None:
    """
    Record (or update) device usage for a subscription on a given day.

    Uses T-SQL MERGE so that re-processing the same telemetry event is safe –
    the row is simply overwritten with the latest value.

    Args:
        subscription_id:   Must match an existing row in ``user_subscriptions``.
        log_date:          Calendar date of the usage event.
        device_type:       E.g. ``"mobile"``, ``"desktop"``, ``"smart_tv"``.
        duration_minutes:  Total active minutes for that day on that device.

    Raises:
        ValueError:      If *duration_minutes* is negative.
        pyodbc.Error:    On any database-level failure (FK violation, etc.).
    """
    if duration_minutes < 0:
        raise ValueError(
            f"duration_minutes must be non-negative, got {duration_minutes}."
        )

    sql = """
        MERGE daily_usage_log AS target
        USING (VALUES (?, ?, ?, ?))
              AS source (subscription_id, log_date, device_type,
                         active_duration_minutes)
        ON  target.subscription_id = source.subscription_id
        AND target.log_date        = source.log_date
        AND target.device_type     = source.device_type
        WHEN MATCHED THEN
            UPDATE SET
                active_duration_minutes = source.active_duration_minutes
        WHEN NOT MATCHED THEN
            INSERT (subscription_id, log_date, device_type,
                    active_duration_minutes)
            VALUES (source.subscription_id, source.log_date,
                    source.device_type, source.active_duration_minutes);
    """
    params = (subscription_id, log_date.isoformat(), device_type, duration_minutes)
    logger.debug(
        "Logging usage – sub='%s', date=%s, device='%s', mins=%d.",
        subscription_id, log_date, device_type, duration_minutes,
    )
    with _get_connection() as conn:
        conn.cursor().execute(sql, params)
    logger.info(
        "Usage logged: '%s' on %s via %s → %d min.",
        subscription_id, log_date, device_type, duration_minutes,
    )


# ---------------------------------------------------------------------------
# ROI / analytics helpers
# ---------------------------------------------------------------------------
def get_usage_summary() -> list[dict]:
    """
    Return per-subscription usage totals joined with cost data for the
    current calendar month.

    Columns returned:
      subscription_id, service_name, monthly_cost, currency,
      unsubscribe_url, total_minutes_this_month, cost_per_minute

    ``cost_per_minute`` is NULL when total usage is zero (division guard).
    Results are ordered by cost_per_minute DESC NULLS FIRST –
    prime cancellation candidates appear at the top.

    Returns:
        List of dicts with keys matching column names.
    """
    sql = """
        SELECT
            s.subscription_id,
            s.service_name,
            s.category,
            s.website_url,
            s.unsubscribe_url,
            s.monthly_cost,
            s.currency,
            s.billing_cycle,
            s.next_billing_date,
            s.current_period_usage_hours,
            s.usage_threshold_hours,
            s.agent_recommendation,
            s.last_sync_timestamp,
            CASE
                WHEN s.current_period_usage_hours = 0 THEN NULL
                ELSE ROUND(
                    CAST(s.monthly_cost AS FLOAT) /
                    (s.current_period_usage_hours * 60), 6)
            END                                           AS cost_per_minute,
            CASE
                WHEN s.usage_threshold_hours IS NOT NULL
                 AND s.current_period_usage_hours < s.usage_threshold_hours
                THEN 1 ELSE 0
            END                                           AS below_threshold
        FROM user_subscriptions s
        ORDER BY
            cost_per_minute DESC;
    """
    with _get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(sql)
        columns = [col[0] for col in cursor.description]
        rows = [dict(zip(columns, row)) for row in cursor.fetchall()]
    logger.debug("Usage summary fetched for %d subscription(s).", len(rows))
    return rows


# ---------------------------------------------------------------------------
# Smoke-test / quick demo  (python azure_db_manager.py)
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    from datetime import timedelta

    initialize_db()

    # Seed subscriptions with full schema
    upsert_subscription(
        subscription_id="netflix_us", service_name="Netflix",
        monthly_cost=15.99, currency="USD", category="streaming",
        website_url="https://www.netflix.com",
        unsubscribe_url="https://www.netflix.com/cancelplan",
        billing_cycle="monthly", next_billing_date=date(2026, 6, 1),
        current_period_usage_hours=42.0, usage_threshold_hours=10.0,
        agent_recommendation="keep",
    )
    upsert_subscription(
        subscription_id="spotify_us", service_name="Spotify",
        monthly_cost=9.99, currency="USD", category="music",
        website_url="https://www.spotify.com",
        unsubscribe_url="https://www.spotify.com/account/subscription/cancel",
        billing_cycle="monthly", next_billing_date=date(2026, 6, 3),
        current_period_usage_hours=1.5, usage_threshold_hours=10.0,
        agent_recommendation="review",
    )
    upsert_subscription(
        subscription_id="hulu_us", service_name="Hulu",
        monthly_cost=7.99, currency="USD", category="streaming",
        website_url="https://www.hulu.com",
        unsubscribe_url="https://secure.hulu.com/account/cancel",
        billing_cycle="monthly", next_billing_date=date(2026, 6, 5),
        current_period_usage_hours=0.0, usage_threshold_hours=10.0,
        agent_recommendation="cancel",
    )

    # Print ROI summary
    print("\n── ROI Summary ──────────────────────────────────────────────────────────")
    print(f"{'Service':<12} {'Category':<14} {'Cost/mo':>8}  {'Usage(h)':>8}  {'Threshold':>9}  {'Rec':>8}  Next Bill")
    print("─" * 90)
    for row in get_usage_summary():
        print(
            f"{row['service_name']:<12} "
            f"{(row['category'] or '—'):<14} "
            f"{float(row['monthly_cost']):>7.2f}  "
            f"{float(row['current_period_usage_hours']):>8.1f}  "
            f"{str(row['usage_threshold_hours'] or '—'):>9}  "
            f"{(row['agent_recommendation'] or '—'):>8}  "
            f"{str(row['next_billing_date'] or '—')}"
        )
    print("─" * 90)
    print("Demo complete. Data is live in Azure SQL.")
