# Gmail Billing Search Agent – Setup Guide

## Quick Start

### Step 1: Create Google Cloud Credentials

1. Go to [Google Cloud Console](https://console.cloud.google.com)
2. Create a new project (or use an existing one)
3. **Enable Gmail API:**
   - Search for "Gmail API" in the top search bar
   - Click the result and press **Enable**
4. **Create OAuth 2.0 Credentials:**
   - Left sidebar → **APIs & Services** → **Credentials**
   - Click **+ Create Credentials** → **OAuth 2.0 Client ID**
   - If prompted, configure the OAuth consent screen first:
     - User Type: **External**
     - Required scopes: Add `Gmail API - .../auth/gmail.readonly`
   - Application type: **Desktop application**
   - Click **Create**
5. **Download the credentials:**
   - In the Credentials page, click the download icon for your OAuth client
   - Save as `gmail_credentials.json` in your project root

### Step 2: Install Dependencies

```bash
pip install -r requirements.txt
```

### Step 3: First Run (OAuth Grant)

Run the agent for the first time:

```bash
python gmail_agent.py
```

- A browser window will open asking you to grant permission
- Click **Allow** to grant the agent access to read your Gmail
- The agent will save a token (`gmail_token.pickle`) for future use

### Step 4: Start Using the Agent

#### Command Line

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

[... more emails ...]

👥 Top billing senders:

1. billing@stripe.com
   Emails: 8
   Sample: Invoice #1234 – Stripe Monthly Payment
```

#### Python Code

```python
from gmail_agent import GmailBillingAgent

# Initialize
agent = GmailBillingAgent()

# Search for billing emails
emails = agent.search_billing_emails(
    keywords=["invoice", "receipt", "billing"],
    days_back=90,
    max_results=20
)

# Get top billing senders
top_senders = agent.get_top_billing_senders(limit=10)

# Search from a specific sender
stripe_emails = agent.search_billing_emails(
    sender="billing@stripe.com",
    days_back=30
)
```

#### FastAPI Integration

Add this to `server.py` for API access:

```python
from gmail_agent import GmailBillingAgent

@app.get("/billing/search")
async def search_billing_emails(
    keywords: str = "invoice,receipt,billing",
    days_back: int = 90,
    max_results: int = 20
):
    """Search Gmail for billing emails."""
    agent = GmailBillingAgent()
    email_list = keywords.split(",")
    results = agent.search_billing_emails(
        keywords=email_list,
        days_back=days_back,
        max_results=max_results
    )
    return {"count": len(results), "emails": results}

@app.get("/billing/top-senders")
async def get_top_senders(days_back: int = 90, limit: int = 10):
    """Get top billing email senders."""
    agent = GmailBillingAgent()
    senders = agent.get_top_billing_senders(days_back=days_back, limit=limit)
    return {"senders": senders}
```

## Environment Variables (Optional)

Add to `.env`:

```env
GMAIL_CREDENTIALS_FILE=gmail_credentials.json
```

## Troubleshooting

### "gmail_credentials.json not found"
- Download credentials from Google Cloud Console (see Step 1)
- Place in project root or set `GMAIL_CREDENTIALS_FILE` env var

### "Invalid grant: Token has been revoked"
- Delete `gmail_token.pickle`
- Run the script again to re-authenticate

### "Gmail API not enabled"
- Go to [Google Cloud Console](https://console.cloud.google.com)
- Search "Gmail API" and click Enable

### "403 Forbidden"
- Make sure OAuth consent screen is configured
- Your credentials type must be "Desktop application"

## API Methods

### `search_billing_emails()`
```python
agent.search_billing_emails(
    keywords=["invoice", "receipt"],   # Search keywords
    sender="billing@stripe.com",        # Optional: filter by sender
    days_back=90,                       # Search last N days
    max_results=20                      # Max emails to return
)
```

Returns: List of dicts with `from`, `subject`, `date`, `amount`, `snippet`

### `get_top_billing_senders()`
```python
agent.get_top_billing_senders(
    days_back=90,   # Search period
    limit=10        # Number of senders to return
)
```

Returns: List of dicts with `sender`, `count`, `sample_subject`

## Security Notes

- `gmail_credentials.json` – Your OAuth credentials (keep safe)
- `gmail_token.pickle` – Your refresh token (don't commit to version control)
- Add both to `.gitignore`:
  ```
  gmail_credentials.json
  gmail_token.pickle
  ```
