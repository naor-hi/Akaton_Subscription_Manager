import os.path
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
import logging

# We only need read-only access to Gmail
SCOPES = ['https://www.googleapis.com/auth/gmail.readonly']

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

def authenticate_gmail():
    """Authenticates the user and returns the Gmail service object."""
    creds = None
    # The file token.json stores the user's access and refresh tokens
    if os.path.exists('token.json'):
        creds = Credentials.from_authorized_user_file('token.json', SCOPES)
    
    # If there are no (valid) credentials available, let the user log in.
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            flow = InstalledAppFlow.from_client_secrets_file(
                'credentials.json', SCOPES)
            creds = flow.run_local_server(port=0)
        # Save the credentials for the next run
        with open('token.json', 'w') as token:
            token.write(creds.to_json())

    return build('gmail', 'v1', credentials=creds)

def fetch_subscription_emails(service, max_results=50):
    """Searches for subscription-related emails."""
    # We ignore very old data to save space, focusing on active/recent billing
    query = "(subject:(receipt OR invoice OR subscription OR renewal OR payment OR billing OR charge OR קבלה OR חשבונית OR תשלום OR חיוב OR מנוי)) OR from:(netflix.com OR spotify.com OR hulu.com OR disneyplus.com OR youtube.com OR amazon.com OR apple.com) newer_than:5y"
    
    logger.info(f"Searching Gmail with query: {query}")
    results = service.users().messages().list(userId='me', q=query, maxResults=max_results).execute()
    messages = results.get('messages', [])

    if not messages:
        logger.info("No subscription emails found.")
        return []
    
    logger.info(f"Found {len(messages)} potential subscription emails.")
    return messages

if __name__ == '__main__':
    # Run this once to trigger the login popup and test the fetcher
    service = authenticate_gmail()
    messages = fetch_subscription_emails(service)