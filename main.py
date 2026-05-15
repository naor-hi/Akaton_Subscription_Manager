import os
import json
import logging
from datetime import datetime
from openai import OpenAI
from dotenv import load_dotenv

# Import our custom modules
from agent_client import AgentClient
from gmail_fetcher import authenticate_gmail, fetch_subscription_emails
from llm_extractor import decode_email_body

load_dotenv()
logging.basicConfig(level=logging.INFO, format='%(levelname)s: %(message)s')
logger = logging.getLogger(__name__)

# Initialize OpenRouter Client
llm_client = OpenAI(
    base_url="https://openrouter.ai/api/v1",
    api_key=os.getenv("OPENROUTER_API_KEY"),
)

def analyze_emails_with_agent(raw_emails_text, existing_subs):
    """Passes the raw emails and current DB state to Gemini for full analysis."""
    
    # We format the existing subscriptions so the LLM knows what IDs already exist
    existing_context = json.dumps(existing_subs, indent=2) if existing_subs else "None"

    system_prompt = f"""
    You are an elite autonomous finance agent.
    Your task is to analyze a batch of raw emails, group them by service, and extract the current subscription state.

    RULES & LOGIC:
    1. MATCHING IDs: Here are the existing subscriptions in the database:
       {existing_context}
       If a receipt matches an existing service (use semantic similarity), you MUST use the exact existing `subscription_id`. 
       If it is a completely new service, invent a clean, lowercase slug (e.g., 'adobe_cc').
    2. CURRENCY TO ILS: The system only accepts ILS. If an email is billed in USD, EUR, etc., convert the `monthly_cost` to ILS using current rough exchange rates (e.g., USD * 3.7). Set `currency` to "ILS".
    3. HISTORICAL INSIGHTS: You are receiving multiple emails. Group them by service. Use the history to populate the `agent_recommendation` field (e.g., "Long-term sub, 12 receipts found" or "Price increased from 30 to 40 ILS").
    4. OUTPUT FORMAT: You must return a JSON object containing a single array called "subscriptions".

    EXPECTED SCHEMA PER SUBSCRIPTION:
    {{
        "subscription_id": "string",
        "service_name": "string",
        "category": "string",
        "monthly_cost": float,
        "currency": "ILS",
        "billing_cycle": "monthly or yearly",
        "agent_recommendation": "string (historical insight)"
    }}
    """

    try:
        response = llm_client.chat.completions.create(
            model="google/gemini-2.5-flash",
            response_format={"type": "json_object"}, 
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": f"Analyze these raw emails:\n\n{raw_emails_text}"}
            ]
        )
        return json.loads(response.choices[0].message.content)
    except Exception as e:
        logger.error(f"LLM Extraction failed: {e}")
        return None

def analyze_roi_with_agent(usage_summary):
    """Analyzes subscription usage vs cost with strict mathematical overrides."""
    
    # Format usage summary for the LLM
    usage_context = json.dumps(usage_summary, indent=2, default=str)

    system_prompt = """
    You are a strict, logical personal finance optimization AI.
    Your task is to analyze each subscription's usage and cost, then recommend whether to keep or cancel.
    
    CRITICAL OVERRIDE RULES (YOU MUST OBEY THESE STRICTLY):
    1. HIGH USAGE OVERRIDE: If `current_period_usage_hours` is greater than 10, you MUST output "Keep - active subscription". You cannot recommend canceling high-usage services.
    2. ZERO USAGE: If `current_period_usage_hours` is 0, you MUST output "Consider canceling".
    3. FREE SERVICES: If `monthly_cost` is 0.0, output "Keep - active subscription" (unless usage is 0, then cancel).
    4. COST EFFICIENCY: For usage between 1 and 10 hours, calculate effective hourly cost (monthly_cost / usage_hours). If > 50 ILS/hour, output "Expensive for the value".

    OUTPUT FORMAT: Return a JSON object with an array "recommendations" where each item has:
    {
        "subscription_id": "string",
        "agent_recommendation": "Keep - active subscription | Review - below threshold | Consider canceling | Expensive for the value",
        "reasoning": "Brief explanation with exact math (e.g., '60 hours of usage justifies the cost')"
    }
    """

    try:
        response = llm_client.chat.completions.create(
            model="google/gemini-2.5-flash",
            response_format={"type": "json_object"},
            temperature=0.0, # Zero temperature forces strict adherence to the rules
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": f"Analyze these subscriptions for ROI:\n\n{usage_context}"}
            ]
        )
        return json.loads(response.choices[0].message.content)
    except Exception as e:
        logger.error(f"ROI analysis failed: {e}")
        return None

def main():
    logger.info("Starting Autonomous Finance Agent...")

    # 1. Initialize Server Client & Get Current State
    server = AgentClient()
    if not server.health_check():
        logger.error("Server is unreachable. Please start your Azure/Local server.")
        return

    existing_subs = server.get_subscriptions()
    logger.info(f"Loaded {len(existing_subs)} existing subscriptions from the database.")

    # 2. Fetch Emails from Gmail
    gmail_service = authenticate_gmail()
    # Grabbing the 50 most recent to ensure we get history but don't overload processing time
    messages = fetch_subscription_emails(gmail_service, max_results=50) 
    
    if not messages:
        logger.info("No emails to process. Agent going to sleep.")
        return

    # 3. Decode Emails into a single text block
    logger.info("Decoding email payloads...")
    combined_email_text = ""
    for msg in messages:
        full_msg = gmail_service.users().messages().get(userId='me', id=msg['id']).execute()
        text = decode_email_body(full_msg.get('payload', {}))
        combined_email_text += f"\n--- EMAIL START ---\n{text[:2000]}\n--- EMAIL END ---\n"

    # 4. Agentic Analysis
    logger.info("Sending batch to Gemini via OpenRouter for analysis & matching...")
    llm_result = analyze_emails_with_agent(combined_email_text, existing_subs)

    if not llm_result or "subscriptions" not in llm_result:
        logger.error("Failed to parse LLM output.")
        return

    # 5. Sync Data Back to Server
    extracted_subs = llm_result["subscriptions"]
    logger.info(f"Agent identified {len(extracted_subs)} active subscriptions. Syncing to server...")

    for sub in extracted_subs:
        try:
            logger.info(f"Upserting: {sub['service_name']} ({sub['monthly_cost']} ILS)")
            server.upsert_subscription(
                subscription_id=sub.get("subscription_id"),
                service_name=sub.get("service_name"),
                monthly_cost=sub.get("monthly_cost"),
                currency=sub.get("currency", "ILS"),
                category=sub.get("category"),
                billing_cycle=sub.get("billing_cycle"),
                agent_recommendation=sub.get("agent_recommendation")
            )
        except Exception as e:
            logger.error(f"Failed to upsert {sub.get('subscription_id')}: {e}")

    logger.info("Agent cycle complete! 🚀")

    # ─────────────────────────────────────────────────────────────────────────
    # Phase 2: ROI Analysis based on Usage Data
    # ─────────────────────────────────────────────────────────────────────────
    logger.info("\n" + "─"*70)
    logger.info("Phase 2: Analyzing ROI based on usage data...")
    logger.info("─"*70)
    
    # Fetch current usage summary from server
    try:
        usage_summary = server.get_summary()
        if not usage_summary:
            logger.info("No usage data available yet. Skipping ROI analysis.")
            return
        
        logger.info(f"Fetched {len(usage_summary)} subscriptions with usage data.")
        
        # Send to LLM for ROI analysis
        logger.info("Sending usage data to Gemini for ROI analysis...")
        roi_result = analyze_roi_with_agent(usage_summary)
        
        if not roi_result or "recommendations" not in roi_result:
            logger.error("Failed to parse ROI analysis output.")
            return
        
        # Update subscriptions with new recommendations
        recommendations = roi_result["recommendations"]
        logger.info(f"Agent generated {len(recommendations)} recommendations. Updating database...")
        
        for rec in recommendations:
            try:
                sub_id = rec.get("subscription_id")
                new_rec = rec.get("agent_recommendation")
                reasoning = rec.get("reasoning", "")
                
                # Find the full subscription data to preserve other fields
                matching_sub = next((s for s in usage_summary if s["subscription_id"] == sub_id), None)
                if not matching_sub:
                    logger.warning(f"Subscription {sub_id} not found in usage summary.")
                    continue
                
                logger.info(f"  {matching_sub['service_name']:20} → {new_rec:25} ({reasoning})")
                
                # Update with new recommendation
                server.upsert_subscription(
                    subscription_id=sub_id,
                    service_name=matching_sub.get("service_name"),
                    monthly_cost=matching_sub.get("monthly_cost", 0),
                    currency=matching_sub.get("currency", "ILS"),
                    category=matching_sub.get("category"),
                    agent_recommendation=new_rec,
                    last_sync_timestamp=datetime.utcnow().isoformat() + "Z"
                )
            except Exception as e:
                logger.error(f"Failed to update recommendation for {sub_id}: {e}")
        
        logger.info("ROI analysis complete! 🎯")
    except Exception as e:
        logger.error(f"ROI analysis phase failed: {e}")
        return

if __name__ == "__main__":
    main()