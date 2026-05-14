"""
GMAIL_AGENT_USAGE.md
--------------------
Complete guide to using the Gmail Billing Search Agent.
"""

# Gmail Billing Search Agent – Usage Guide

## What You Get

Your subscription manager now includes a **Gmail Billing Search Agent** that can:

✅ Search Gmail for invoice and billing emails  
✅ Extract sender, date, subject, and amount  
✅ Find top billing service providers  
✅ Automatically sync billing data to your Azure database  
✅ Integrate with FastAPI REST endpoints  

---

## Installation

### 1. Update Dependencies

```bash
pip install -r requirements.txt
```

### 2. Get Google OAuth Credentials

Follow the steps in [GMAIL_SETUP.md](GMAIL_SETUP.md):

1. Create Google Cloud project
2. Enable Gmail API
3. Download OAuth credentials → save as `gmail_credentials.json`

### 3. First Run

```bash
python gmail_agent.py
```

- Browser will open for permission grant
- Agent saves token automatically (`gmail_token.pickle`)

---

## Basic Usage

### Command Line – Search for Billing Emails

```bash
python gmail_agent.py
```

Output:
```
📧 Searching for billing emails...

Found 12 billing emails:

1. Invoice #1234 – Stripe Monthly Payment
   From: billing@stripe.com
   Date: Mon, 13 May 2026 10:32:00 +0000
   Amount: $99.00
   Preview: Thank you for using Stripe...

2. Receipt: GitHub Copilot Subscription
   From: noreply@github.com
   Date: Sun, 12 May 2026 15:22:00 +0000
   Amount: $19.99
   Preview: Thank you for your purchase...

[... 10 more emails ...]

👥 Top billing senders:

1. billing@stripe.com
   Emails: 8
   Sample: Invoice #1234 – Stripe Monthly Payment

2. noreply@github.com
   Emails: 3
   Sample: Receipt: GitHub Copilot Subscription
```

### Command Line – Sync to Database

```bash
# Sync last 30 days
python gmail_to_db_sync.py --sync-recent

# Sync last 90 days
python gmail_to_db_sync.py --sync-all

# Show top senders
python gmail_to_db_sync.py --top-senders
```

### Python Code – Search Emails

```python
from gmail_agent import GmailBillingAgent

agent = GmailBillingAgent()

# Search for invoices and receipts
results = agent.search_billing_emails(
    keywords=["invoice", "receipt"],
    days_back=90,
    max_results=20
)

for email in results:
    print(f"{email['subject']}")
    print(f"  From: {email['from']}")
    print(f"  Amount: {email['amount']}")
    print(f"  Date: {email['date']}\n")
```

### Python Code – Search by Sender

```python
from gmail_agent import GmailBillingAgent

agent = GmailBillingAgent()

# Get all emails from Stripe
stripe_invoices = agent.search_billing_emails(
    sender="billing@stripe.com",
    days_back=180,
    max_results=50
)

print(f"Found {len(stripe_invoices)} invoices from Stripe")
```

### Python Code – Get Top Senders

```python
from gmail_agent import GmailBillingAgent

agent = GmailBillingAgent()

top_senders = agent.get_top_billing_senders(
    days_back=90,
    limit=5
)

for sender_info in top_senders:
    print(f"{sender_info['sender']}: {sender_info['count']} emails")
    print(f"  Subject: {sender_info['sample_subject']}")
```

### Python Code – Sync to Database

```python
from gmail_to_db_sync import GmailToDBSync

syncer = GmailToDBSync()

# Sync last 30 days of emails
results = syncer.sync_gmail_to_db(days_back=30)

print(f"Processed: {results['processed']}")
print(f"Added to DB: {results['added']}")
print(f"Skipped: {results['skipped']}")
print(f"Errors: {results['errors']}")
```

---

## FastAPI Integration

Add these endpoints to your `server.py`:

```python
from gmail_agent import GmailBillingAgent
from gmail_to_db_sync import GmailToDBSync

# ... existing imports ...

@app.get("/api/billing/search")
async def search_billing_emails(
    keywords: str = "invoice,receipt,billing",
    days_back: int = 90,
    max_results: int = 20
):
    """
    Search Gmail for billing emails.
    
    Query parameters:
      - keywords: Comma-separated keywords (default: "invoice,receipt,billing")
      - days_back: Number of days to search (default: 90)
      - max_results: Max emails to return (default: 20)
    
    Returns: { count, emails: [ {from, subject, date, amount, snippet} ] }
    """
    try:
        agent = GmailBillingAgent()
        email_list = [kw.strip() for kw in keywords.split(",")]
        results = agent.search_billing_emails(
            keywords=email_list,
            days_back=days_back,
            max_results=max_results
        )
        return {
            "count": len(results),
            "emails": results,
            "status": "success"
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/billing/senders")
async def get_top_billing_senders(days_back: int = 90, limit: int = 10):
    """
    Get top billing email senders.
    
    Query parameters:
      - days_back: Search period (default: 90)
      - limit: Number of senders (default: 10)
    
    Returns: { senders: [ {sender, count, sample_subject} ] }
    """
    try:
        agent = GmailBillingAgent()
        senders = agent.get_top_billing_senders(
            days_back=days_back,
            limit=limit
        )
        return {
            "count": len(senders),
            "senders": senders,
            "status": "success"
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/billing/sync")
async def sync_gmail_to_database(days_back: int = 30, max_results: int = 50):
    """
    Sync Gmail billing emails to subscription database.
    
    Query parameters:
      - days_back: Number of days to search (default: 30)
      - max_results: Max emails to process (default: 50)
    
    Returns: { processed, added, skipped, errors }
    """
    try:
        syncer = GmailToDBSync()
        results = syncer.sync_gmail_to_db(
            days_back=days_back,
            max_results=max_results
        )
        return {
            **results,
            "status": "success"
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/billing/sender/{sender_email}")
async def search_by_sender(sender_email: str, days_back: int = 90):
    """
    Search emails from a specific sender.
    
    Path parameters:
      - sender_email: Email address to search
    
    Query parameters:
      - days_back: Number of days to search (default: 90)
    
    Returns: { count, emails: [ ... ] }
    """
    try:
        agent = GmailBillingAgent()
        results = agent.search_billing_emails(
            sender=sender_email,
            days_back=days_back,
            max_results=50
        )
        return {
            "sender": sender_email,
            "count": len(results),
            "emails": results,
            "status": "success"
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
```

### Test the API Endpoints

```bash
# Search for billing emails
curl "http://localhost:8000/api/billing/search?keywords=invoice&days_back=90"

# Get top senders
curl "http://localhost:8000/api/billing/senders?limit=10"

# Sync Gmail to database
curl -X POST "http://localhost:8000/api/billing/sync?days_back=30"

# Search from specific sender
curl "http://localhost:8000/api/billing/sender/billing@stripe.com"
```

---

## Scheduling Automatic Sync

### Option 1: Windows Task Scheduler

```batch
# Create a scheduled task to run every day at 8 AM
schtasks /create /tn "Gmail Billing Sync" /tr "python c:\path\to\gmail_to_db_sync.py --sync-recent" /sc daily /st 08:00
```

### Option 2: Python APScheduler

```python
from apscheduler.schedulers.background import BackgroundScheduler
from gmail_to_db_sync import GmailToDBSync

scheduler = BackgroundScheduler()

def sync_job():
    syncer = GmailToDBSync()
    syncer.sync_gmail_to_db(days_back=7)  # Daily, check last 7 days

# Run every day at 8 AM
scheduler.add_job(sync_job, 'cron', hour=8, minute=0)
scheduler.start()
```

### Option 3: Azure Functions

Deploy `gmail_to_db_sync.py` as an Azure Function with a timer trigger (daily at 8 AM).

---

## Email Parser Customization

Edit `_extract_subscription_from_email()` in `gmail_to_db_sync.py` to add more service providers:

```python
mappings = {
    # ... existing mappings ...
    "myservice": ["myservice.com", "billing@myservice.com"],
    "another": ["another.io"],
}
```

---

## Troubleshooting

| Problem | Solution |
|---------|----------|
| `gmail_credentials.json not found` | Download from Google Cloud Console (see GMAIL_SETUP.md) |
| `403 Forbidden` | Re-run `gmail_agent.py` to re-authenticate |
| No emails found | Try broader keywords or increase `days_back` |
| Amount not extracted | Check email format; may need custom regex |
| Database sync fails | Verify Azure credentials in `.env` |

---

## Files Created

- **gmail_agent.py** – Core Gmail search agent
- **gmail_to_db_sync.py** – Sync Gmail → Azure database
- **GMAIL_SETUP.md** – Google Cloud setup guide
- **GMAIL_AGENT_USAGE.md** – This file

---

## Next Steps

1. ✅ Set up Google Cloud credentials (GMAIL_SETUP.md)
2. ✅ Run `python gmail_agent.py` to test
3. ✅ Sync emails to database: `python gmail_to_db_sync.py --sync-recent`
4. ✅ Add FastAPI endpoints (see above)
5. ✅ Set up automatic daily syncs (optional)

---

## API Reference

### GmailBillingAgent

```python
from gmail_agent import GmailBillingAgent

agent = GmailBillingAgent()

# Search emails
agent.search_billing_emails(
    keywords: List[str] = None,
    sender: str = None,
    days_back: int = 90,
    max_results: int = 20
) -> List[Dict]

# Get top senders
agent.get_top_billing_senders(
    days_back: int = 90,
    limit: int = 10
) -> List[Dict]
```

### GmailToDBSync

```python
from gmail_to_db_sync import GmailToDBSync

syncer = GmailToDBSync()

# Sync to database
syncer.sync_gmail_to_db(
    days_back: int = 30,
    max_results: int = 50
) -> Dict

# Get top senders
syncer.sync_top_senders(
    days_back: int = 90
) -> Dict
```

---

**Need help?** See [GMAIL_SETUP.md](GMAIL_SETUP.md) for setup or check the code comments in `gmail_agent.py`.
