"""
gmail_to_db_sync.py
-------------------
Sync Gmail billing emails to Azure subscription database.

This module extracts billing information from Gmail and stores it
in your subscription database for tracking and analysis.

Usage:
  python gmail_to_db_sync.py --sync-recent

Or import as a module:
  from gmail_to_db_sync import sync_gmail_to_db
  sync_gmail_to_db(days_back=30)
"""

import logging
import re
from datetime import datetime, timedelta
from typing import List, Dict, Optional
import argparse

from gmail_agent import GmailBillingAgent
import azure_db_manager as db

logger = logging.getLogger(__name__)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s – %(message)s",
)


class GmailToDBSync:
    """Sync Gmail billing data to Azure SQL database."""

    def __init__(self):
        """Initialize Gmail agent and database connection."""
        self.gmail_agent = GmailBillingAgent()
        self.db_manager = db.AzureDBManager()

    def sync_gmail_to_db(self, days_back: int = 30, max_results: int = 50) -> Dict:
        """
        Search Gmail for billing emails and add to database.

        Args:
            days_back: Search emails from last N days
            max_results: Maximum emails to process

        Returns:
            Dict with sync results: {processed, added, skipped, errors}
        """
        logger.info(f"🔄 Starting Gmail-to-DB sync (last {days_back} days)...")

        keywords = [
            "invoice",
            "receipt",
            "billing",
            "payment",
            "charge",
            "subscription",
        ]

        # Search for billing emails
        emails = self.gmail_agent.search_billing_emails(
            keywords=keywords, days_back=days_back, max_results=max_results
        )

        results = {"processed": 0, "added": 0, "skipped": 0, "errors": 0}

        if not emails:
            logger.info("No billing emails found")
            return results

        logger.info(f"Found {len(emails)} emails to process")

        for email in emails:
            results["processed"] += 1

            try:
                # Extract subscription details from email
                subscription_data = self._extract_subscription_from_email(email)

                if not subscription_data:
                    logger.debug(f"Skipped email from {email['from']} – no data extracted")
                    results["skipped"] += 1
                    continue

                # Check if subscription already exists
                existing = self.db_manager.query(
                    """
                    SELECT subscription_id FROM subscriptions
                    WHERE LOWER(service_name) = LOWER(?)
                    AND LOWER(email_from) = LOWER(?)
                    """,
                    (subscription_data["service_name"], email["from"]),
                )

                if existing:
                    logger.info(
                        f"✓ Subscription '{subscription_data['service_name']}' "
                        f"already in database"
                    )
                    results["skipped"] += 1
                else:
                    # Add new subscription to database
                    self.db_manager.upsert_subscription(
                        service_name=subscription_data["service_name"],
                        email_from=email["from"],
                        subject=email["subject"],
                        amount=subscription_data.get("amount"),
                        billing_date=subscription_data.get("billing_date"),
                        billing_cycle=subscription_data.get("billing_cycle"),
                        notes=email["snippet"][:500],
                    )

                    logger.info(f"✓ Added subscription: {subscription_data['service_name']}")
                    results["added"] += 1

            except Exception as e:
                logger.error(f"Error processing email from {email['from']}: {e}")
                results["errors"] += 1

        return results

    @staticmethod
    def _extract_subscription_from_email(email: Dict) -> Optional[Dict]:
        """
        Extract subscription details from email.

        Args:
            email: Email dict from Gmail agent

        Returns:
            Dict with {service_name, amount, billing_date, billing_cycle}
        """
        subject = email.get("subject", "").lower()
        snippet = email.get("snippet", "").lower()
        from_email = email.get("from", "")
        amount = email.get("amount")

        # Extract service name from sender
        service_name = GmailToDBSync._extract_service_name(from_email, subject)

        if not service_name:
            return None

        # Extract amount from snippet if not already found
        if not amount:
            amount = GmailToDBSync._extract_amount(snippet)

        # Try to determine billing cycle
        billing_cycle = GmailToDBSync._detect_billing_cycle(subject + " " + snippet)

        # Parse date
        try:
            billing_date = email.get("date", "")
        except:
            billing_date = None

        return {
            "service_name": service_name,
            "amount": amount,
            "billing_date": billing_date,
            "billing_cycle": billing_cycle,
        }

    @staticmethod
    def _extract_service_name(from_email: str, subject: str) -> Optional[str]:
        """
        Extract service name from sender email or subject.

        Examples:
          billing@stripe.com → Stripe
          noreply@aws.amazon.com → AWS
          invoices@github.com → GitHub
        """
        from_email = from_email.lower()
        subject = subject.lower()

        # Manual mappings for common services
        mappings = {
            "stripe": ["stripe.com"],
            "aws": ["aws.amazon.com", "amazon.com"],
            "github": ["github.com"],
            "azure": ["microsoft.com", "azure.microsoft.com"],
            "gcp": ["google.com", "gcp.com"],
            "digitalocean": ["digitalocean.com"],
            "vercel": ["vercel.com"],
            "slack": ["slack.com"],
            "microsoft": ["microsoft.com"],
            "apple": ["apple.com"],
            "netflix": ["netflix.com"],
            "spotify": ["spotify.com"],
            "adobe": ["adobe.com"],
            "jetbrains": ["jetbrains.com"],
            "figma": ["figma.com"],
        }

        # Check from_email
        for service, domains in mappings.items():
            for domain in domains:
                if domain in from_email:
                    return service.capitalize()

        # Check subject for service names
        for service in mappings.keys():
            if service in subject:
                return service.capitalize()

        # Fallback: extract domain name
        if "@" in from_email:
            domain = from_email.split("@")[1].split(".")[0]
            if domain and domain not in ["noreply", "no-reply", "mail", "notification"]:
                return domain.capitalize()

        return None

    @staticmethod
    def _extract_amount(text: str) -> Optional[str]:
        """
        Extract currency amount from text.

        Matches patterns like: $99.99, £10.00, €50, etc.
        """
        match = re.search(r"[$£€¥₹₽][\d,]+\.?\d*", text)
        return match.group(0) if match else None

    @staticmethod
    def _detect_billing_cycle(text: str) -> Optional[str]:
        """
        Detect billing cycle from text.

        Returns: "monthly", "yearly", "quarterly", etc.
        """
        text = text.lower()

        if "annual" in text or "yearly" in text or "per year" in text:
            return "yearly"
        elif "quarterly" in text:
            return "quarterly"
        elif "monthly" in text or "per month" in text:
            return "monthly"
        elif "weekly" in text or "per week" in text:
            return "weekly"

        return None

    def sync_top_senders(self, days_back: int = 90) -> Dict:
        """
        Get top billing senders and add to database.

        Args:
            days_back: Search period in days

        Returns:
            Dict with results
        """
        logger.info(f"Fetching top billing senders (last {days_back} days)...")

        top_senders = self.gmail_agent.get_top_billing_senders(
            days_back=days_back, limit=20
        )

        results = {"processed": len(top_senders), "details": []}

        for sender_info in top_senders:
            details = {
                "sender": sender_info["sender"],
                "email_count": sender_info["count"],
                "sample": sender_info["sample_subject"],
            }
            results["details"].append(details)
            logger.info(f"{sender_info['sender']} – {sender_info['count']} emails")

        return results


def main():
    """Command-line interface for Gmail sync."""
    parser = argparse.ArgumentParser(
        description="Sync Gmail billing emails to subscription database"
    )
    parser.add_argument(
        "--sync-recent",
        action="store_true",
        help="Sync billing emails from the last 30 days",
    )
    parser.add_argument(
        "--sync-all",
        action="store_true",
        help="Sync billing emails from the last 90 days",
    )
    parser.add_argument(
        "--top-senders",
        action="store_true",
        help="Show top billing senders",
    )
    parser.add_argument(
        "--days", type=int, default=30, help="Number of days to search (default: 30)"
    )

    args = parser.parse_args()

    syncer = GmailToDBSync()

    if args.sync_recent:
        logger.info("Syncing recent billing emails (30 days)...")
        results = syncer.sync_gmail_to_db(days_back=30)
        print(f"\n✓ Sync complete:")
        print(f"  Processed: {results['processed']}")
        print(f"  Added: {results['added']}")
        print(f"  Skipped: {results['skipped']}")
        print(f"  Errors: {results['errors']}")

    elif args.sync_all:
        logger.info("Syncing all billing emails (90 days)...")
        results = syncer.sync_gmail_to_db(days_back=90)
        print(f"\n✓ Sync complete:")
        print(f"  Processed: {results['processed']}")
        print(f"  Added: {results['added']}")
        print(f"  Skipped: {results['skipped']}")
        print(f"  Errors: {results['errors']}")

    elif args.top_senders:
        logger.info("Fetching top billing senders...")
        results = syncer.sync_top_senders(days_back=args.days)
        print(f"\n👥 Top {len(results['details'])} billing senders:\n")
        for i, sender in enumerate(results["details"], 1):
            print(f"{i}. {sender['sender']}")
            print(f"   Emails: {sender['email_count']}")
            print(f"   Sample: {sender['sample']}\n")

    else:
        parser.print_help()


if __name__ == "__main__":
    main()
