"""
agent_client.py
---------------
HTTP client for the Autonomous Finance Agent.

The agent imports this module to read/write data from the Azure server.
No direct database access – everything goes through the REST API in server.py.

Usage example:
    from agent_client import AgentClient

    client = AgentClient()                        # reads AGENT_API_URL from .env
    client.upsert_subscription("netflix_us", "Netflix", 15.99)
    client.log_usage("netflix_us", date.today(), "smart_tv", 90)
    summary = client.get_summary()                # list of dicts, ready for LLM
"""

import logging
import os
from datetime import date
from typing import Optional

import requests
from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Client class
# ---------------------------------------------------------------------------

class AgentClient:
    """
    Thin HTTP wrapper around the Finance Agent REST API.

    All methods raise AgentClientError on non-2xx responses so the agent
    can catch a single exception type.
    """

    def __init__(self, base_url: Optional[str] = None, timeout: int = 15):
        """
        Args:
            base_url: Root URL of the server, e.g.
                      ``"https://finance-agent-app.azurewebsites.net"``.
                      Falls back to the ``AGENT_API_URL`` env var, then
                      ``http://localhost:8000`` for local dev.
            timeout:  Request timeout in seconds.
        """
        self.base_url = (
            base_url
            or os.getenv("AGENT_API_URL", "http://localhost:8000")
        ).rstrip("/")
        self.timeout = timeout
        self.session = requests.Session()
        self.session.headers.update({"Content-Type": "application/json"})
        logger.info("AgentClient initialised → %s", self.base_url)

    # ── internal helper ──────────────────────────────────────────────────────

    def _post(self, path: str, payload: dict) -> dict:
        url = f"{self.base_url}{path}"
        try:
            resp = self.session.post(url, json=payload, timeout=self.timeout)
            resp.raise_for_status()
            return resp.json()
        except requests.HTTPError as exc:
            raise AgentClientError(
                f"POST {url} failed [{exc.response.status_code}]: "
                f"{exc.response.text}"
            ) from exc
        except requests.RequestException as exc:
            raise AgentClientError(f"POST {url} network error: {exc}") from exc

    def _get(self, path: str) -> list | dict:
        url = f"{self.base_url}{path}"
        try:
            resp = self.session.get(url, timeout=self.timeout)
            resp.raise_for_status()
            return resp.json()
        except requests.HTTPError as exc:
            raise AgentClientError(
                f"GET {url} failed [{exc.response.status_code}]: "
                f"{exc.response.text}"
            ) from exc
        except requests.RequestException as exc:
            raise AgentClientError(f"GET {url} network error: {exc}") from exc

    def _delete(self, path: str, params: Optional[dict] = None) -> dict:
        url = f"{self.base_url}{path}"
        try:
            resp = self.session.delete(url, params=params, timeout=self.timeout)
            resp.raise_for_status()
            return resp.json()
        except requests.HTTPError as exc:
            raise AgentClientError(
                f"DELETE {url} failed [{exc.response.status_code}]: "
                f"{exc.response.text}"
            ) from exc
        except requests.RequestException as exc:
            raise AgentClientError(f"DELETE {url} network error: {exc}") from exc

    # ── public API ───────────────────────────────────────────────────────────

    def health_check(self) -> bool:
        """
        Returns True if the server is reachable and healthy.
        Safe to call before any other operation.
        """
        try:
            result = self._get("/health")
            return result.get("status") == "ok"
        except AgentClientError:
            return False

    def upsert_subscription(
        self,
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
    ) -> dict:
        """
        Send a subscription to the server (insert or update).

        Fields that are unknown at call time can be left as defaults —
        None values are stripped from the payload so they never overwrite
        existing DB data with nulls. This allows different agents to update
        different fields independently (e.g. one updates cost, another updates usage).

        Args:
            subscription_id:            Stable slug, e.g. ``"netflix_us"``.
            service_name:               Human-readable name, e.g. ``"Netflix"``.
            monthly_cost:               Monthly charge in *currency* units.
            currency:                   ISO-4217 code, default ``"USD"``.
            category:                   e.g. ``"Streaming"``, ``"Productivity"``.
            website_url:                Main website URL.
            unsubscribe_url:            Direct cancellation link.
            billing_cycle:              ``"Monthly"``, ``"Yearly"``, etc.
            next_billing_date:          Next charge date as ``"YYYY-MM-DD"`` string.
            current_period_usage_hours: Hours used this billing period.
            usage_threshold_hours:      Hours/month below which agent flags for review.
            agent_recommendation:       ``"Keep"``, ``"Consider_Canceling"``, ``"Unused"``, or None.
            last_sync_timestamp:        ISO-8601 datetime string, e.g. ``"2026-05-14T19:00:00Z"``.

        Returns:
            Server response dict, e.g. ``{"status": "ok", "message": "..."}``.
        """
        payload = {
            "subscription_id": subscription_id,
            "service_name": service_name,
            "category": category,
            "website_url": website_url,
            "unsubscribe_url": unsubscribe_url,
            "monthly_cost": monthly_cost,
            "currency": currency,
            "billing_cycle": billing_cycle,
            "next_billing_date": next_billing_date,
            "current_period_usage_hours": current_period_usage_hours,
            "usage_threshold_hours": usage_threshold_hours,
            "agent_recommendation": agent_recommendation,
            "last_sync_timestamp": last_sync_timestamp,
        }
        # Strip None values – prevents overwriting existing DB fields with nulls
        # when only a subset of fields is known at call time.
        payload = {k: v for k, v in payload.items() if v is not None}
        logger.debug("upsert_subscription → %s", subscription_id)
        return self._post("/subscriptions", payload)

    def get_subscriptions(self) -> list[dict]:
        """
        Fetch all tracked subscriptions from the server.

        Returns:
            List of subscription dicts ordered by service name.
        """
        logger.debug("get_subscriptions ←")
        return self._get("/subscriptions")

    def delete_subscription(self, subscription_id: str) -> dict:
        """
        Permanently delete a subscription and ALL its usage history.

        The server cascades the delete to ``daily_usage_log`` automatically.

        Args:
            subscription_id: The subscription to remove.

        Returns:
            Server response dict with ``status`` and ``message``.

        Raises:
            AgentClientError: If the subscription does not exist (404)
                              or any other server error occurs.
        """
        logger.debug("delete_subscription → %s", subscription_id)
        return self._delete(f"/subscriptions/{subscription_id}")

    def delete_usage_log(
        self,
        subscription_id: str,
        log_date: Optional[date] = None,
        device_type: Optional[str] = None,
    ) -> dict:
        """
        Delete usage log entries for a subscription with optional filters.

        Combinations:
          • No filters          → deletes ALL history for that subscription
          • log_date only       → deletes all devices for that specific day
          • device_type only    → deletes all days for that device
          • Both filters        → deletes exactly one row

        Args:
            subscription_id: Which subscription's logs to target.
            log_date:        Optional – filter to a specific date.
            device_type:     Optional – filter to a specific device.

        Returns:
            Server response dict including how many rows were deleted.
        """
        params = {}
        if log_date is not None:
            params["log_date"] = log_date.isoformat()
        if device_type is not None:
            params["device_type"] = device_type
        logger.debug(
            "delete_usage_log → %s (date=%s, device=%s)",
            subscription_id, log_date, device_type,
        )
        return self._delete(f"/usage/{subscription_id}", params=params or None)

    def get_raw_subscriptions(self) -> list[dict]:
        """
        Fetch all subscriptions in RawSubscriptionData format.

        Called by the Analysis Engine before it starts processing.
        Returns the read-only fields + device_usage_hours for each subscription.

        Returns:
            List of dicts matching the RawSubscriptionData schema.
        """
        logger.debug("get_raw_subscriptions ←")
        return self._get("/api/v1/subscriptions/raw")

    def post_analyzed_subscriptions(self, records: list[dict]) -> dict:
        """
        Send Analysis Engine results back to the server.

        Each dict in *records* must contain:
          - subscription_id            (str)
          - current_period_usage_hours (float)
          - usage_threshold_hours      (float)
          - agent_recommendation       (str: "Keep" | "Consider_Canceling" | "Unused")
          - last_sync_timestamp        (str ISO-8601, e.g. "2026-05-14T19:00:00Z")

        The server updates ONLY these four analysis columns — all other
        subscription fields are left untouched.

        Args:
            records: List of AnalyzedSubscriptionData dicts.

        Returns:
            Server response, e.g. ``{"status": "success", "message": "..."}``.
        """
        logger.debug("post_analyzed_subscriptions → %d record(s)", len(records))
        return self._post("/api/v1/subscriptions/analyzed", records)

    def get_summary(self) -> list[dict]:
        """
        Fetch the ROI summary from the server.

        This is the primary input for the agent's LLM decision engine.
        Each dict contains:
          - subscription_id, service_name, monthly_cost, currency
          - unsubscribe_url
          - total_minutes_this_month
          - cost_per_minute  (None = zero usage → top cancel candidate)

        Returns:
            List of dicts ordered by cost_per_minute DESC (most wasteful first).
        """
        logger.debug("get_summary ←")
        return self._get("/summary")

    def format_summary_for_llm(self) -> str:
        """
        Fetches the summary and formats it as plain-text ready to inject
        directly into an LLM prompt.

        Uses current_period_usage_hours vs usage_threshold_hours to flag
        subscriptions for review or cancellation.

        Returns:
            Multi-line string the agent can paste straight into its prompt.
        """
        rows = self.get_summary()
        if not rows:
            return "No subscriptions found."

        lines = [
            "Subscription ROI Report (current billing period):",
            f"{'Service':<14} {'Category':<14} {'Cost/mo':>8}  {'Usage(h)':>8}  {'Threshold':>9}  {'Rec':>8}  Next Bill       Action",
            "─" * 100,
        ]
        for r in rows:
            usage   = r.get("current_period_usage_hours") or 0
            thresh  = r.get("usage_threshold_hours")
            rec     = r.get("agent_recommendation") or "—"
            below   = r.get("below_threshold", 0)

            if usage == 0:
                action = "⚠️  CANCEL – zero usage"
            elif below:
                action = "🟡 Review – below threshold"
            else:
                action = "✅ Keep"

            lines.append(
                f"{r['service_name']:<14} "
                f"{(r.get('category') or '—'):<14} "
                f"{float(r['monthly_cost']):>7.2f}  "
                f"{float(usage):>8.1f}  "
                f"{str(thresh or '—'):>9}  "
                f"{rec:>8}  "
                f"{str(r.get('next_billing_date') or '—'):<16}"
                f"{action}"
            )
        lines.append("─" * 100)
        return "\n".join(lines)


# ---------------------------------------------------------------------------
# Custom exception
# ---------------------------------------------------------------------------

class AgentClientError(Exception):
    """Raised when the server returns a non-2xx response or is unreachable."""


# ---------------------------------------------------------------------------
# Smoke-test  (python agent_client.py)
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    import sys

    logging.basicConfig(level=logging.INFO)

    client = AgentClient()

    # 1. Health check
    if not client.health_check():
        print("❌ Server is not reachable. Is it running?")
        print("   Start it with:  uvicorn server:app --reload --port 8000")
        sys.exit(1)
    print("✅ Server is healthy\n")

    # 2. Upsert subscriptions with full schema
    client.upsert_subscription(
        "netflix_us", "Netflix", 15.99, category="streaming",
        website_url="https://www.netflix.com",
        unsubscribe_url="https://www.netflix.com/cancelplan",
        next_billing_date=date(2026, 6, 1),
        current_period_usage_hours=42.0, usage_threshold_hours=10.0,
        agent_recommendation="keep",
    )
    client.upsert_subscription(
        "spotify_us", "Spotify", 9.99, category="music",
        website_url="https://www.spotify.com",
        unsubscribe_url="https://www.spotify.com/account/subscription/cancel",
        next_billing_date=date(2026, 6, 3),
        current_period_usage_hours=1.5, usage_threshold_hours=10.0,
        agent_recommendation="review",
    )
    client.upsert_subscription(
        "hulu_us", "Hulu", 7.99, category="streaming",
        website_url="https://www.hulu.com",
        unsubscribe_url="https://secure.hulu.com/account/cancel",
        next_billing_date=date(2026, 6, 5),
        current_period_usage_hours=0.0, usage_threshold_hours=10.0,
        agent_recommendation="cancel",
    )
    print("✅ Subscriptions upserted\n")

    # 3. Print LLM-ready summary
    print(client.format_summary_for_llm())

    # 4. Delete usage log demo
    print("\n── Delete Tests ─────────────────────────────────────────────")
    r = client.delete_usage_log("spotify_us", log_date=date.today())
    print("delete_usage_log (today only):", r["message"])

    r = client.delete_usage_log("hulu_us")
    print("delete_usage_log (all history):", r["message"])

    r = client.delete_subscription("hulu_us")
    print("delete_subscription:", r["message"])
