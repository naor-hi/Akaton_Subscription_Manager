"""seed_demo.py – Insert 5 demo subscriptions into Azure SQL."""
import azure_db_manager as db
from datetime import date

subscriptions = [
    dict(
        subscription_id="netflix_il", service_name="Netflix",
        monthly_cost=54.90, currency="ILS", category="Streaming",
        website_url="https://www.netflix.com",
        unsubscribe_url="https://www.netflix.com/cancelplan",
        billing_cycle="Monthly", next_billing_date=date(2026, 6, 3),
        current_period_usage_hours=0.0, usage_threshold_hours=10.0,
        agent_recommendation=None,
    ),
    dict(
        subscription_id="spotify_il", service_name="Spotify",
        monthly_cost=19.90, currency="ILS", category="Music",
        website_url="https://www.spotify.com",
        unsubscribe_url="https://www.spotify.com/account/subscription/cancel",
        billing_cycle="Monthly", next_billing_date=date(2026, 6, 7),
        current_period_usage_hours=0.0, usage_threshold_hours=10.0,
        agent_recommendation=None,
    ),
    dict(
        subscription_id="microsoft365_il", service_name="Microsoft 365",
        monthly_cost=34.90, currency="ILS", category="Productivity",
        website_url="https://www.microsoft.com/microsoft-365",
        unsubscribe_url="https://account.microsoft.com/services",
        billing_cycle="Monthly", next_billing_date=date(2026, 6, 12),
        current_period_usage_hours=0.0, usage_threshold_hours=15.0,
        agent_recommendation=None,
    ),
    dict(
        subscription_id="youtube_premium_il", service_name="YouTube Premium",
        monthly_cost=23.90, currency="ILS", category="Streaming",
        website_url="https://www.youtube.com/premium",
        unsubscribe_url="https://www.youtube.com/paid_memberships",
        billing_cycle="Monthly", next_billing_date=date(2026, 6, 18),
        current_period_usage_hours=0.0, usage_threshold_hours=8.0,
        agent_recommendation=None,
    ),
    dict(
        subscription_id="canva_pro_il", service_name="Canva Pro",
        monthly_cost=55.00, currency="ILS", category="Design",
        website_url="https://www.canva.com",
        unsubscribe_url="https://www.canva.com/settings/billing",
        billing_cycle="Monthly", next_billing_date=date(2026, 6, 22),
        current_period_usage_hours=0.0, usage_threshold_hours=5.0,
        agent_recommendation=None,
    ),
]

for s in subscriptions:
    db.upsert_subscription(**s)

print("All 5 subscriptions inserted.\n")

rows = db.get_all_subscriptions()
print(f"{len(rows)} subscriptions in DB:")
print(f"{'Service':<18} {'Category':<14} {'Cost (ILS)':>10}  Next Bill")
print("-" * 58)
for r in rows:
    print(
        f"{r['service_name']:<18} "
        f"{(r['category'] or '-'):<14} "
        f"{float(r['monthly_cost']):>10.2f}  "
        f"{r['next_billing_date']}"
    )
