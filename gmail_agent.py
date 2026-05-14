"""
gmail_agent.py
--------------
Gmail search agent for finding subscription billing emails.

Features:
  - OAuth2 authentication with Gmail API
  - Search for billing-related emails
  - Extract sender, subject, date, and snippet
  - Filter by keywords, date range, and sender

Usage:
  agent = GmailBillingAgent()
  results = agent.search_billing_emails(keywords=["invoice", "receipt"])

Setup:
  1. Create a Google Cloud project at console.cloud.google.com
  2. Enable Gmail API
  3. Create OAuth 2.0 credentials (Desktop app)
  4. Download credentials JSON → save as 'gmail_credentials.json'
  5. Store in project root or set GMAIL_CREDENTIALS_FILE env var
"""

import logging
import os
import pickle
import base64
import re
from datetime import datetime, timedelta
from email.mime.text import MIMEText
from typing import List, Dict, Optional
from pathlib import Path

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from google.api_python_client import discovery
from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger(__name__)

# Gmail API scopes
SCOPES = ["https://www.googleapis.com/auth/gmail.readonly"]

# Credentials file path
CREDENTIALS_FILE = os.getenv("GMAIL_CREDENTIALS_FILE", "gmail_credentials.json")
TOKEN_PICKLE = "gmail_token.pickle"


class GmailBillingAgent:
    """Agent to search and extract billing information from Gmail."""

    def __init__(self, credentials_file: str = CREDENTIALS_FILE):
        """
        Initialize Gmail API client with OAuth2 authentication.

        Args:
            credentials_file: Path to Google OAuth2 credentials JSON file
        """
        self.credentials_file = credentials_file
        self.service = None
        self._authenticate()

    def _authenticate(self):
        """Authenticate with Gmail API using OAuth2."""
        creds = None

        # Load token from pickle if it exists
        if os.path.exists(TOKEN_PICKLE):
            with open(TOKEN_PICKLE, "rb") as token:
                creds = pickle.load(token)

        # If credentials are invalid or expired, refresh or re-authenticate
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        elif not creds or not creds.valid:
            if not os.path.exists(self.credentials_file):
                raise FileNotFoundError(
                    f"Gmail credentials file not found: {self.credentials_file}\n"
                    "Download from Google Cloud Console and save as 'gmail_credentials.json'"
                )

            flow = InstalledAppFlow.from_client_secrets_file(
                self.credentials_file, SCOPES
            )
            creds = flow.run_local_server(port=0)

            # Save token for future use
            with open(TOKEN_PICKLE, "wb") as token:
                pickle.dump(creds, token)

        self.service = discovery.build("gmail", "v1", credentials=creds)
        logger.info("✓ Gmail API authenticated successfully")

    def search_billing_emails(
        self,
        keywords: Optional[List[str]] = None,
        sender: Optional[str] = None,
        days_back: int = 90,
        max_results: int = 20,
    ) -> List[Dict]:
        """
        Search for billing emails in Gmail.

        Args:
            keywords: List of keywords to search (e.g., ["invoice", "receipt", "billing"])
            sender: Email address or domain to filter by (e.g., "billing@stripe.com")
            days_back: Search emails from the last N days (default 90)
            max_results: Maximum number of results to return (default 20)

        Returns:
            List of email dicts with: id, from, subject, date, snippet
        """
        if not self.service:
            raise RuntimeError("Gmail API not authenticated")

        # Build search query
        query_parts = []

        if keywords:
            keyword_query = " OR ".join([f'"{kw}"' for kw in keywords])
            query_parts.append(f"({keyword_query})")

        if sender:
            query_parts.append(f'from:{sender}')

        # Add date filter
        since_date = (datetime.now() - timedelta(days=days_back)).strftime("%Y/%m/%d")
        query_parts.append(f"after:{since_date}")

        query = " ".join(query_parts)

        logger.info(f"Searching Gmail with query: {query}")

        try:
            # Search for emails
            results = (
                self.service.users()
                .messages()
                .list(userId="me", q=query, maxResults=max_results)
                .execute()
            )

            messages = results.get("messages", [])

            if not messages:
                logger.info("No billing emails found")
                return []

            logger.info(f"Found {len(messages)} emails, fetching details...")

            # Fetch full details for each email
            emails = []
            for msg in messages:
                email_data = self._get_message_details(msg["id"])
                if email_data:
                    emails.append(email_data)

            return emails

        except Exception as e:
            logger.error(f"Error searching Gmail: {e}")
            return []

    def _get_message_details(self, message_id: str) -> Optional[Dict]:
        """
        Fetch and parse a single email message.

        Args:
            message_id: Gmail message ID

        Returns:
            Dict with email metadata: from, subject, date, snippet, amount
        """
        try:
            msg = (
                self.service.users()
                .messages()
                .get(userId="me", id=message_id, format="full")
                .execute()
            )

            headers = msg["payload"]["headers"]
            snippet = msg.get("snippet", "")

            # Extract key fields
            email_dict = {
                "id": message_id,
                "from": self._get_header(headers, "From"),
                "subject": self._get_header(headers, "Subject"),
                "date": self._get_header(headers, "Date"),
                "snippet": snippet,
                "amount": self._extract_amount(snippet),
            }

            return email_dict

        except Exception as e:
            logger.warning(f"Error fetching message {message_id}: {e}")
            return None

    @staticmethod
    def _get_header(headers: List[Dict], name: str) -> str:
        """Extract header value from email headers."""
        for header in headers:
            if header["name"] == name:
                return header["value"]
        return ""

    @staticmethod
    def _extract_amount(text: str) -> Optional[str]:
        """
        Extract currency amount from email text.

        Matches patterns like: $99.99, £10.00, €50, etc.
        """
        match = re.search(r"[$£€¥₹₽][\d,]+\.?\d*", text)
        return match.group(0) if match else None

    def get_top_billing_senders(self, days_back: int = 90, limit: int = 10) -> List[Dict]:
        """
        Get the top billing email senders (by frequency).

        Args:
            days_back: Search period in days
            limit: Maximum number of senders to return

        Returns:
            List of dicts: {sender, count, sample_subject}
        """
        keywords = ["invoice", "receipt", "billing", "payment", "charge", "order"]
        emails = self.search_billing_emails(
            keywords=keywords, days_back=days_back, max_results=100
        )

        # Group by sender
        sender_counts = {}
        for email in emails:
            sender = email["from"]
            if sender not in sender_counts:
                sender_counts[sender] = {
                    "count": 0,
                    "sample_subject": email["subject"],
                }
            sender_counts[sender]["count"] += 1

        # Sort by count and return top N
        sorted_senders = sorted(
            sender_counts.items(), key=lambda x: x[1]["count"], reverse=True
        )[:limit]

        return [
            {"sender": sender, **details} for sender, details in sorted_senders
        ]


def main():
    """Example usage of GmailBillingAgent."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s – %(message)s",
    )

    # Initialize agent
    agent = GmailBillingAgent()

    # Search for billing emails from the last 90 days
    print("\n📧 Searching for billing emails...\n")
    billing_emails = agent.search_billing_emails(
        keywords=["invoice", "receipt", "billing", "payment"],
        days_back=90,
        max_results=15,
    )

    if billing_emails:
        print(f"Found {len(billing_emails)} billing emails:\n")
        for i, email in enumerate(billing_emails, 1):
            print(f"{i}. {email['subject']}")
            print(f"   From: {email['from']}")
            print(f"   Date: {email['date']}")
            print(f"   Amount: {email['amount'] or 'N/A'}")
            print(f"   Preview: {email['snippet'][:80]}...\n")
    else:
        print("No billing emails found.")

    # Get top billing senders
    print("\n👥 Top billing senders:\n")
    top_senders = agent.get_top_billing_senders(days_back=90, limit=5)
    for i, sender_info in enumerate(top_senders, 1):
        print(f"{i}. {sender_info['sender']}")
        print(f"   Emails: {sender_info['count']}")
        print(f"   Sample: {sender_info['sample_subject']}\n")


if __name__ == "__main__":
    main()
