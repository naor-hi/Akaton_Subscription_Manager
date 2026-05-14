# Azure SQL Database – Setup Guide

Step-by-step instructions to provision the cloud database and connect the agent.

---

## Step 1 – Create an Azure SQL Server & Database

1. Go to [portal.azure.com](https://portal.azure.com) → **Create a resource** → **SQL Database**
2. Fill in the form:
   | Field | Recommended value |
   |---|---|
   | **Resource group** | `rg-finance-agent` (create new) |
   | **Database name** | `subscription_manager` |
   | **Server** | Create new → pick a unique name, e.g. `finance-agent-srv` |
   | **Authentication** | SQL authentication (set admin login + password) |
   | **Compute + storage** | Click *Configure* → choose **Free offer** (32 GB, free for 12 months) or **Basic** ($5/mo) |
   | **Backup redundancy** | Locally redundant (cheapest for a hackathon) |

3. Click **Review + Create** → **Create**. Wait ~2 minutes.

---

## Step 2 – Allow your IP through the Firewall

1. Open the new **SQL Server** resource (not the database).
2. Left menu → **Networking** → **Firewall rules**
3. Click **Add your client IPv4 address** → **Save**
4. Also toggle **Allow Azure services and resources to access this server** → **Yes**
   (needed if you deploy to Azure App Service / Functions later)

---

## Step 3 – Fill in your .env file

Open `.env` in the project root and replace the placeholder values:

```env
AZURE_SQL_SERVER=finance-agent-srv.database.windows.net
AZURE_SQL_DATABASE=subscription_manager
AZURE_SQL_USERNAME=your-admin-login
AZURE_SQL_PASSWORD=your-strong-password
AZURE_SQL_DRIVER=ODBC Driver 18 for SQL Server
```

> **Where to find the server name:**  
> Azure Portal → your SQL Database → **Overview** → **Server name**

---

## Step 4 – Install the ODBC Driver (Windows)

Check if it's already installed:
```powershell
Get-OdbcDriver -Name "ODBC Driver 18 for SQL Server"
```

If not found, download and install from Microsoft:
```
https://aka.ms/downloadmsodbcsql
```

---

## Step 5 – Install Python dependencies

```powershell
.venv\Scripts\python.exe -m pip install -r requirements.txt
```

---

## Step 6 – Initialise the schema & run the smoke test

```powershell
.venv\Scripts\python.exe azure_db_manager.py
```

Expected output:
```
[INFO] Initialising Azure SQL database 'subscription_manager' on server '...' …
[INFO] Schema ready (user_subscriptions, daily_usage_log).
[INFO] Subscription 'netflix_us' saved.
...
── ROI Summary ──────────────────────────────────────────────
Service         Cost/mo    Mins      $/min  Unsubscribe URL
...
```

---

## Step 7 – (Optional) Deploy to Azure App Service

If you want the agent itself to run in the cloud:

```powershell
# Install Azure CLI
winget install Microsoft.AzureCLI

# Login
az login

# Create App Service plan (free tier)
az appservice plan create --name finance-agent-plan --resource-group rg-finance-agent --sku FREE --is-linux

# Create the web app
az webapp create --name finance-agent-app --resource-group rg-finance-agent --plan finance-agent-plan --runtime "PYTHON:3.11"

# Push env vars (instead of .env file in the cloud)
az webapp config appsettings set --name finance-agent-app --resource-group rg-finance-agent --settings \
  AZURE_SQL_SERVER="finance-agent-srv.database.windows.net" \
  AZURE_SQL_DATABASE="subscription_manager" \
  AZURE_SQL_USERNAME="your-admin-login" \
  AZURE_SQL_PASSWORD="your-strong-password" \
  AZURE_SQL_DRIVER="ODBC Driver 18 for SQL Server"
```

> **Security tip:** For production, use **Azure Key Vault** references instead of plain-text app settings.

---

## Architecture Diagram

```
┌─────────────────────────────────┐
│   Your Machine / Azure App Svc  │
│                                 │
│  azure_db_manager.py            │
│  (pyodbc + ODBC Driver 18)      │
│         │                       │
│         │  TLS 1.2 encrypted    │
└─────────┼───────────────────────┘
          │
          ▼
┌─────────────────────────────────┐
│   Azure SQL Database            │
│   finance-agent-srv             │
│                                 │
│   ┌─────────────────────────┐   │
│   │  user_subscriptions     │   │
│   ├─────────────────────────┤   │
│   │  daily_usage_log        │   │
│   └─────────────────────────┘   │
└─────────────────────────────────┘
```
