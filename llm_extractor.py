import os
import base64
import json
from openai import OpenAI
from dotenv import load_dotenv

load_dotenv()

# Initialize OpenRouter Client pointing to Gemini 2.5 Flash
client = OpenAI(
    base_url="https://openrouter.ai/api/v1",
    api_key=os.getenv("OPENROUTER_API_KEY"),
)

def decode_email_body(payload):
    """Recursively extracts and decodes the email body from a Gmail API payload."""
    body = ""
    if 'parts' in payload:
        for part in payload['parts']:
            if part['mimeType'] == 'text/plain':
                data = part['body'].get('data', '')
                body += base64.urlsafe_b64decode(data).decode('utf-8', errors='ignore')
            elif 'parts' in part: # Handle nested parts
                body += decode_email_body(part)
    else:
        data = payload.get('body', {}).get('data', '')
        if data:
            body = base64.urlsafe_b64decode(data).decode('utf-8', errors='ignore')
    return body

def extract_subscription_data(email_text):
    """Sends the raw email to Gemini to extract a strict JSON object."""
    
    # We only extract fields relevant to the receipt right now. 
    # Usage hours and recommendations happen in Phase 3.
    system_prompt = """
    You are an autonomous finance agent. Extract subscription and billing data from this raw email text.
    Return ONLY a valid JSON object. If a field cannot be found, return null.
    
    EXPECTED SCHEMA:
    {
        "subscription_id": "A short, unique, lowercase slug (e.g., 'netflix', 'adobe_cc')",
        "service_name": "Clean name of the service (e.g., 'Netflix')",
        "category": "e.g., 'streaming', 'software', 'fitness', 'music'",
        "monthly_cost": Float value of the recurring cost (e.g., 15.99),
        "currency": "3-letter ISO code (e.g., 'USD', 'ILS', 'EUR')",
        "billing_cycle": "Strictly 'monthly' or 'yearly'",
        "next_billing_date": "YYYY-MM-DD"
    }
    """

    try:
        response = client.chat.completions.create(
            model="google/gemini-2.5-flash",
            # Enforce JSON output mode
            response_format={"type": "json_object"}, 
            messages=[
                {"role": "system", "content": system_prompt},
                # We slice the text to avoid hitting limits with massive HTML tracking blocks
                {"role": "user", "content": f"Extract data from this email:\n\n{email_text[:6000]}"}
            ]
        )
        return json.loads(response.choices[0].message.content)
    except Exception as e:
        print(f"Error during LLM extraction: {e}")
        return None

# --- Quick Test ---
if __name__ == '__main__':
    # A dummy email to test the LLM connection before wiring it to Gmail
    sample_email = """
    Thank you for your Spotify Premium subscription!
    Your receipt for this month is $10.99. 
    We will charge your card again on June 15, 2026.
    """
    
    print("Sending to OpenRouter...")
    result = extract_subscription_data(sample_email)
    print(json.dumps(result, indent=2))