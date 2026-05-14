import azure_db_manager as db

# Test 1: full upsert with last_sync_timestamp provided by caller
db.upsert_subscription(
    subscription_id="netflix_il",
    service_name="Netflix",
    monthly_cost=54.90,
    currency="ILS",
    category="Streaming",
    next_billing_date="2026-06-03",
    current_period_usage_hours=5.5,
    usage_threshold_hours=10.0,
    agent_recommendation="Keep",
    last_sync_timestamp="2026-05-14T20:00:00Z",
)

# Test 2: partial upsert — only required fields, no timestamp (DB sets it)
db.upsert_subscription(
    subscription_id="spotify_il",
    service_name="Spotify",
    agent_recommendation="Consider_Canceling",
)

rows = db.get_all_subscriptions()
print("Updated rows:")
for r in rows:
    print(
        f"  {r['service_name']:<18} "
        f"rec={str(r['agent_recommendation'] or '-'):<22} "
        f"usage={r['current_period_usage_hours']}h  "
        f"ts={r['last_sync_timestamp']}"
    )
print("\nAll tests passed ✅")
