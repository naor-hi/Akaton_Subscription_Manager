"""migrate_schema.py – Widen agent_recommendation column to NVARCHAR(32)."""
import azure_db_manager as db

with db._get_connection() as conn:
    conn.cursor().execute("""
        ALTER TABLE user_subscriptions
        ALTER COLUMN agent_recommendation NVARCHAR(32) NULL;
    """)
print("Migration complete: agent_recommendation → NVARCHAR(32) ✅")
