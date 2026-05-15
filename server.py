"""
server.py
---------
FastAPI REST server – runs on Azure App Service (or locally for dev).

The agent calls this API to read/write data.
The server talks to Azure SQL via azure_db_manager.py.

Endpoints:
  POST   /subscriptions                    – upsert a subscription
  GET    /subscriptions                    – list all subscriptions
  DELETE /subscriptions/{subscription_id}  – delete subscription + all its usage history
  DELETE /usage/{subscription_id}          – delete usage logs (optional date/device filters)
  GET    /summary                          – ROI summary (prime input for the agent)
  GET    /health                           – liveness probe for Azure

  ── Analysis Engine (external client) ──
  GET    /api/v1/subscriptions/raw         – raw data for the Analysis Engine
  POST   /api/v1/subscriptions/analyzed    – write engine results back to DB

Run locally:
  uvicorn server:app --reload --port 8000

Deploy to Azure App Service:
  See AZURE_SETUP.md – Step 7
"""

import logging
from datetime import date, datetime
from typing import Optional

from fastapi import FastAPI, HTTPException, Query, status
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

import azure_db_manager as db

# ---------------------------------------------------------------------------
# App setup
# ---------------------------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s – %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)

app = FastAPI(
    title="Autonomous Finance Agent – API",
    description="Read/write subscription and usage data for the AI agent.",
    version="1.0.0",
)

# Allow the agent (running anywhere) to call this API
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


# ---------------------------------------------------------------------------
# Startup – ensure schema exists
# ---------------------------------------------------------------------------
@app.on_event("startup")
def on_startup():
    logger.info("Server starting – initialising database schema …")
    db.initialize_db()
    logger.info("Server ready.")


# ---------------------------------------------------------------------------
# Pydantic models (request / response shapes)
# ---------------------------------------------------------------------------
class SubscriptionIn(BaseModel):
    subscription_id: str              = Field(...,       example="netflix_us")
    service_name: str                 = Field(...,       example="Netflix")
    category: Optional[str]           = Field(None,      example="Streaming")
    website_url: Optional[str]        = Field(None,      example="https://www.netflix.com")
    unsubscribe_url: Optional[str]    = Field(None,      example="https://netflix.com/cancelplan")
    monthly_cost: float               = Field(0.0, ge=0, example=15.99)
    currency: str                     = Field("USD",     min_length=3, max_length=3, example="USD")
    billing_cycle: str                = Field("monthly", example="Monthly")
    next_billing_date: Optional[str]  = Field(None,      example="2026-06-01")
    current_period_usage_hours: float = Field(0.0,  ge=0, example=42.0)
    usage_threshold_hours: float      = Field(0.0,  ge=0, example=10.0)
    agent_recommendation: Optional[str]    = Field(None, example="Keep")
    last_sync_timestamp: Optional[str]     = Field(None, example="2026-05-14T19:00:00Z")


class SubscriptionOut(SubscriptionIn):
    pass


class SummaryRow(BaseModel):
    subscription_id: str
    service_name: str
    category: Optional[str]
    website_url: Optional[str]
    unsubscribe_url: Optional[str]
    monthly_cost: float
    currency: str
    billing_cycle: str
    next_billing_date: Optional[date]
    current_period_usage_hours: float
    usage_threshold_hours: Optional[float]
    agent_recommendation: Optional[str]
    last_sync_timestamp: Optional[str]
    cost_per_minute: Optional[float]
    below_threshold: int


class StatusResponse(BaseModel):
    status: str
    message: str


# ── Device models ─────────────────────────────────────────────────────────────

class DeviceIn(BaseModel):
    device_id:   str           = Field(...,  example="device-iphone-amit")
    device_name: str           = Field(...,  example="Amit's iPhone")
    device_type: str           = Field(...,  example="mobile")
    owner_label: Optional[str] = Field(None, example="Amit")


class DeviceOut(DeviceIn):
    registered_at: Optional[str] = None
    last_seen_at:  Optional[str] = None


class DeviceUsageIn(BaseModel):
    device_id:       str   = Field(..., example="device-iphone-amit")
    subscription_id: str   = Field(..., example="netflix_il")
    usage_date:      date  = Field(..., example="2026-05-14")
    usage_hours:     float = Field(..., ge=0, example=1.5)


class DeviceUsageSummaryRow(BaseModel):
    subscription_id:      str
    service_name:         str
    monthly_cost:         float
    currency:             str
    unsubscribe_url:      Optional[str]
    agent_recommendation: Optional[str]
    usage_threshold_hours: Optional[float]
    total_usage_hours:    float
    cost_per_hour:        Optional[float]


class DeviceBreakdownRow(BaseModel):
    device_id:             str
    device_name:           str
    device_type:           str
    owner_label:           Optional[str]
    total_hours_this_month: float


# ── Analysis Engine models ────────────────────────────────────────────────────

class RawSubscriptionData(BaseModel):
    """Shape returned to the Analysis Engine – read-only fields only."""
    subscription_id:   str            = Field(..., example="550e8400-e29b-41d4-a716-446655440000")
    service_name:      str            = Field(..., example="Spotify")
    category:          Optional[str]  = Field(None, example="Music")
    website_url:       Optional[str]  = Field(None, example="https://spotify.com")
    unsubscribe_url:   Optional[str]  = Field(None, example="https://spotify.com/cancel")
    monthly_cost:      float          = Field(..., example=19.9)
    currency:          str            = Field(..., example="ILS")
    billing_cycle:     str            = Field(..., example="Monthly")
    next_billing_date: Optional[date] = Field(None, example="2026-06-01")
    device_usage_hours: float         = Field(0.0,  example=2.5)


class AnalyzedSubscriptionData(BaseModel):
    """Shape the Analysis Engine POSTs back after processing."""
    subscription_id:            str      = Field(..., example="550e8400-e29b-41d4-a716-446655440000")
    current_period_usage_hours: float    = Field(..., example=12.5)
    usage_threshold_hours:      float    = Field(..., example=10.0)
    agent_recommendation:       str      = Field(..., example="Keep")
    last_sync_timestamp:        datetime = Field(..., example="2026-05-14T19:00:00Z")


class AnalyzedBatchResponse(BaseModel):
    status:  str = Field(..., example="success")
    message: str = Field(..., example="Analyzed data updated successfully")


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@app.get("/health", tags=["Infra"])
def health_check():
    """Azure liveness probe – always returns 200 if the server is up."""
    return {"status": "ok"}


# ── Subscriptions ────────────────────────────────────────────────────────────

@app.post(
    "/subscriptions",
    status_code=status.HTTP_200_OK,
    response_model=StatusResponse,
    tags=["Subscriptions"],
    summary="Upsert a subscription (insert or update)",
)
def upsert_subscription(payload: SubscriptionIn):
    """
    Called by the agent (or email parser) when a new receipt is detected.
    Safe to call multiple times – idempotent upsert.
    """
    try:
        db.upsert_subscription(
            subscription_id=payload.subscription_id,
            service_name=payload.service_name,
            monthly_cost=payload.monthly_cost,
            currency=payload.currency,
            category=payload.category,
            website_url=payload.website_url,
            unsubscribe_url=payload.unsubscribe_url,
            billing_cycle=payload.billing_cycle,
            next_billing_date=payload.next_billing_date,
            current_period_usage_hours=payload.current_period_usage_hours,
            usage_threshold_hours=payload.usage_threshold_hours,
            agent_recommendation=payload.agent_recommendation,
            last_sync_timestamp=payload.last_sync_timestamp,
        )
        return {"status": "ok", "message": f"Subscription '{payload.subscription_id}' saved."}
    except Exception as exc:
        logger.error("upsert_subscription failed: %s", exc)
        raise HTTPException(status_code=500, detail=str(exc))


@app.get(
    "/subscriptions",
    response_model=list[SubscriptionOut],
    tags=["Subscriptions"],
    summary="List all tracked subscriptions",
)
def get_subscriptions():
    """Returns every subscription ordered by service name."""
    try:
        rows = db.get_all_subscriptions()
        result = []
        for r in rows:
            d = dict(r)
            if d.get('next_billing_date'):
                d['next_billing_date'] = d['next_billing_date'].isoformat()
            if d.get('last_sync_timestamp'):
                d['last_sync_timestamp'] = d['last_sync_timestamp'].isoformat()
            result.append(d)
        return result
    except Exception as exc:
        logger.error("get_subscriptions failed: %s", exc)
        raise HTTPException(status_code=500, detail=str(exc))


@app.delete(
    "/subscriptions/{subscription_id}",
    response_model=StatusResponse,
    tags=["Subscriptions"],
    summary="Delete a subscription and all its usage history",
)
def delete_subscription(subscription_id: str):
    """
    Permanently removes the subscription row and, via ON DELETE CASCADE,
    all associated daily_usage_log rows.

    Returns 404 if the subscription_id does not exist.
    """
    try:
        deleted = db.delete_subscription(subscription_id)
        if not deleted:
            raise HTTPException(
                status_code=404,
                detail=f"Subscription '{subscription_id}' not found.",
            )
        return {
            "status": "ok",
            "message": f"Subscription '{subscription_id}' and its usage history deleted.",
        }
    except HTTPException:
        raise
    except Exception as exc:
        logger.error("delete_subscription failed: %s", exc)
        raise HTTPException(status_code=500, detail=str(exc))


# ── Usage log ────────────────────────────────────────────────────────────────

@app.delete(
    "/usage/{subscription_id}",
    response_model=StatusResponse,
    tags=["Usage"],
    summary="Delete usage log entries (flexible filtering)",
)
def delete_usage(
    subscription_id: str,
    log_date: Optional[date] = Query(
        None,
        description="Filter to a specific date (YYYY-MM-DD). Omit to delete all dates.",
    ),
    device_type: Optional[str] = Query(
        None,
        description="Filter to a specific device type. Omit to delete all devices.",
    ),
):
    """
    Delete usage log rows for a subscription with optional filters:

    - No filters → deletes **all** history for that subscription
    - `log_date` only → deletes all devices for that day
    - `device_type` only → deletes all days for that device
    - Both → deletes exactly one row
    """
    try:
        count = db.delete_usage_log(subscription_id, log_date, device_type)
        return {
            "status": "ok",
            "message": f"Deleted {count} usage log row(s) for '{subscription_id}'.",
        }
    except Exception as exc:
        logger.error("delete_usage failed: %s", exc)
        raise HTTPException(status_code=500, detail=str(exc))


# ── Device registry ──────────────────────────────────────────────────────────

@app.post(
    "/devices",
    response_model=StatusResponse,
    tags=["Devices"],
    summary="Register a device or update its info",
)
def register_device(payload: DeviceIn):
    """
    Called once per device on first run (or whenever device info changes).
    Idempotent — safe to call on every startup.
    """
    try:
        db.register_device(
            device_id=payload.device_id,
            device_name=payload.device_name,
            device_type=payload.device_type,
            owner_label=payload.owner_label,
        )
        return {"status": "ok", "message": f"Device '{payload.device_id}' registered."}
    except Exception as exc:
        logger.error("register_device failed: %s", exc)
        raise HTTPException(status_code=500, detail=str(exc))


@app.get(
    "/devices",
    response_model=list[DeviceOut],
    tags=["Devices"],
    summary="List all registered devices",
)
def get_devices():
    """Returns every registered device ordered by name."""
    try:
        return db.get_all_devices()
    except Exception as exc:
        logger.error("get_devices failed: %s", exc)
        raise HTTPException(status_code=500, detail=str(exc))


@app.delete(
    "/devices/{device_id}",
    response_model=StatusResponse,
    tags=["Devices"],
    summary="Delete a device and all its usage history",
)
def delete_device(device_id: str):
    """Permanently removes the device and all its device_usage rows (CASCADE)."""
    try:
        deleted = db.delete_device(device_id)
        if not deleted:
            raise HTTPException(status_code=404, detail=f"Device '{device_id}' not found.")
        return {"status": "ok", "message": f"Device '{device_id}' deleted."}
    except HTTPException:
        raise
    except Exception as exc:
        logger.error("delete_device failed: %s", exc)
        raise HTTPException(status_code=500, detail=str(exc))


# ── Device usage ──────────────────────────────────────────────────────────────

@app.post(
    "/devices/usage",
    response_model=StatusResponse,
    tags=["Devices"],
    summary="Log usage from a specific device (idempotent)",
)
def log_device_usage(payload: DeviceUsageIn):
    """
    Called by each device to report how many hours it used a subscription.
    Re-sending the same (device_id, subscription_id, usage_date) overwrites
    the hours — safe to call multiple times.
    """
    try:
        db.log_device_usage(
            device_id=payload.device_id,
            subscription_id=payload.subscription_id,
            usage_date=payload.usage_date,
            usage_hours=payload.usage_hours,
        )
        return {
            "status": "ok",
            "message": (
                f"Usage logged: device='{payload.device_id}' "
                f"sub='{payload.subscription_id}' "
                f"date={payload.usage_date} → {payload.usage_hours}h"
            ),
        }
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    except Exception as exc:
        logger.error("log_device_usage failed: %s", exc)
        raise HTTPException(status_code=500, detail=str(exc))


@app.get(
    "/devices/usage/summary",
    response_model=list[DeviceUsageSummaryRow],
    tags=["Devices"],
    summary="Aggregated usage per subscription across all devices (current month)",
)
def get_device_usage_summary():
    """
    Returns total usage hours per subscription aggregated from ALL devices
    for the current calendar month. Ordered least-used first.
    """
    try:
        return db.get_device_usage_summary()
    except Exception as exc:
        logger.error("get_device_usage_summary failed: %s", exc)
        raise HTTPException(status_code=500, detail=str(exc))


@app.get(
    "/devices/usage/breakdown/{subscription_id}",
    response_model=list[DeviceBreakdownRow],
    tags=["Devices"],
    summary="Per-device usage breakdown for one subscription",
)
def get_device_breakdown(subscription_id: str):
    """
    Shows how many hours each device contributed to a subscription
    in the current month. Useful for the agent to see which device
    is driving (or not driving) usage.
    """
    try:
        return db.get_per_device_breakdown(subscription_id)
    except Exception as exc:
        logger.error("get_device_breakdown failed: %s", exc)
        raise HTTPException(status_code=500, detail=str(exc))


# ── Analysis Engine endpoints (api/v1) ───────────────────────────────────────

@app.get(
    "/api/v1/subscriptions/raw",
    response_model=list[RawSubscriptionData],
    tags=["Analysis Engine"],
    summary="Fetch raw subscription data for the Analysis Engine",
)
def get_raw_subscriptions():
    """
    Called by the external Python Analysis Engine to pull all subscription
    records with their current device_usage_hours for processing.

    Returns an array of RawSubscriptionData objects.
    """
    try:
        rows = db.get_raw_subscriptions()
        return rows
    except Exception as exc:
        logger.error("get_raw_subscriptions failed: %s", exc)
        raise HTTPException(status_code=500, detail=str(exc))


@app.post(
    "/api/v1/subscriptions/analyzed",
    response_model=AnalyzedBatchResponse,
    tags=["Analysis Engine"],
    summary="Write Analysis Engine results back to the database",
)
def post_analyzed_subscriptions(payload: list[AnalyzedSubscriptionData]):
    """
    Called by the Analysis Engine after it has aggregated usage hours
    and computed recommendations.

    Updates ONLY the four analysis columns per subscription_id:
      • current_period_usage_hours
      • usage_threshold_hours
      • agent_recommendation
      • last_sync_timestamp

    All other fields (service_name, monthly_cost, etc.) are left untouched.
    """
    try:
        records = [
            {
                "subscription_id":            r.subscription_id,
                "current_period_usage_hours": r.current_period_usage_hours,
                "usage_threshold_hours":      r.usage_threshold_hours,
                "agent_recommendation":       r.agent_recommendation,
                "last_sync_timestamp":        r.last_sync_timestamp.isoformat(),
            }
            for r in payload
        ]
        updated = db.update_analyzed_subscriptions(records)
        return {
            "status":  "success",
            "message": f"Analyzed data updated successfully ({updated} record(s) written).",
        }
    except Exception as exc:
        logger.error("post_analyzed_subscriptions failed: %s", exc)
        raise HTTPException(status_code=500, detail=str(exc))


# ── ROI summary ──────────────────────────────────────────────────────────────

@app.get(
    "/summary",
    response_model=list[SummaryRow],
    tags=["Analytics"],
    summary="ROI summary – prime input for the agent's decision engine",
)
def get_summary():
    """
    Returns per-subscription cost vs. usage for the current month.
    Subscriptions with NULL cost_per_minute have zero usage → top cancel candidates.
    Ordered by cost_per_minute DESC (most wasteful first).
    """
    try:
        rows = db.get_usage_summary()
        result = []
        for r in rows:
            d = dict(r)
            # Convert date/datetime objects to ISO strings for JSON serialization
            if d.get('next_billing_date'):
                d['next_billing_date'] = d['next_billing_date'].isoformat()
            if d.get('last_sync_timestamp'):
                d['last_sync_timestamp'] = d['last_sync_timestamp'].isoformat()
            result.append(d)
        return result
    except Exception as exc:
        logger.error("get_summary failed: %s", exc)
        raise HTTPException(status_code=500, detail=str(exc))
