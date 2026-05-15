import streamlit as st
import pandas as pd
import os
import json
import re
import urllib.parse
import streamlit.components.v1 as components
from openai import OpenAI
from agent_client import AgentClient
from datetime import datetime
from dotenv import load_dotenv

# --- 1. Setup & Style ---
load_dotenv()
st.set_page_config(page_title="Agent Command Center", page_icon="🤖", layout="wide")

st.markdown("""
    <style>
    section[data-testid="stSidebar"] {
        background-color: #121417;
    }
    .stChatMessage {
        border-radius: 10px;
        padding: 10px;
        margin-bottom: 10px;
    }
    div[data-testid="stForm"] {
        border: none;
        padding: 0;
    }
    </style>
    """, unsafe_allow_html=True)

llm_client = OpenAI(
    base_url="https://openrouter.ai/api/v1",
    api_key=os.getenv("OPENROUTER_API_KEY"),
)

# --- 2. Helper Functions ---
@st.cache_data(ttl=60)
def fetch_subscription_data():
    client = AgentClient()
    if not client.health_check():
        return None
    return client.get_summary()

def get_cancellation_url(service_name):
    prompt = f"Provide ONLY the direct cancellation URL for {service_name}. Do not include any other text. Just the raw https:// link."
    try:
        response = llm_client.chat.completions.create(
            model="google/gemini-2.5-flash",
            messages=[{"role": "user", "content": prompt}],
            temperature=0.1 
        )
        url = response.choices[0].message.content.strip()
        if url.startswith("http"):
            return url
    except Exception as e:
        return None
    return None

def draft_negotiation_email(service_name, cost, usage_hours):
    """Dynamically drafts a negotiation email based on usage."""
    if float(usage_hours) > 10:
        tactic = f"I use your service extensively ({usage_hours} hours this month) and want to remain a loyal customer, but the cost of {cost} ILS is getting difficult to justify. Are there any retention or loyalty discounts available?"
    else:
        tactic = f"I am currently reviewing my finances and realized I only used {service_name} for {usage_hours} hours this month. I am preparing to cancel my subscription due to the {cost} ILS cost, but wanted to check if you have a lower-tier or retention rate available before I pull the plug."

    prompt = f"""
    Write a short, highly professional email to the customer support team of {service_name}.
    
    CORE ARGUMENT: {tactic}
    
    RULES:
    1. Do not use placeholders like [Company Name]. Fill them in with {service_name}.
    2. The email must sign off exactly as: "Akaton Autonomous Agent, on behalf of Naor".
    3. Return ONLY a JSON object with this exact format:
    {{
        "support_email": "support@{service_name.lower().replace(' ', '')}.com",
        "subject": "Account Review - Retention Request",
        "body": "The full email body text..."
    }}
    """
    try:
        response = llm_client.chat.completions.create(
            model="google/gemini-2.5-flash",
            messages=[{"role": "user", "content": prompt}],
            response_format={"type": "json_object"},
            temperature=0.2 
        )
        return json.loads(response.choices[0].message.content)
    except Exception as e:
        return None

def get_agent_response(user_input, context_data, history):
    system_prompt = f"""
    You are the Akaton Financial Agent (Gemini 2.5 Flash). You are autonomous and proactive.

    CORE COMMANDS:
    1. WEB ACCESS: Use your internal knowledge to provide specific recommendations.
    2. DATA CONTEXT: Use the user's data to give highly personalized advice.
    3. AUTONOMOUS ACTIONS (CRITICAL): 
    If a user asks for a recommendation for a new service, and you make a definitive recommendation, you MUST append a secret action tag at the very end of your response in this exact format:
    [ACTION_OPEN_URL: https://www.direct-url-to-service.com]
    
    USER DATA:
    {json.dumps(context_data, indent=2)}
    
    Tone: Proactive, sharp, and decisive.
    """
    messages = [{"role": "system", "content": system_prompt}] + history + [{"role": "user", "content": user_input}]
    response = llm_client.chat.completions.create(
        model="google/gemini-2.5-flash",
        messages=messages,
        temperature=0.7
    )
    return response.choices[0].message.content

# --- 3. Sidebar Agent Copilot ---
with st.sidebar:
    st.title("💬 Agent Copilot")
    st.caption("Gemini 2.5 Flash | Autonomous Web Actions")
    
    chat_container = st.container(height=550)
    
    if "messages" not in st.session_state:
        st.session_state.messages = []

    with chat_container:
        for message in st.session_state.messages:
            with st.chat_message(message["role"]):
                st.markdown(message["content"])

    if prompt := st.chat_input("Ask for a recommendation or action..."):
        st.session_state.messages.append({"role": "user", "content": prompt})
        with chat_container:
            with st.chat_message("user"):
                st.markdown(prompt)

            with st.chat_message("assistant"):
                current_data = fetch_subscription_data()
                raw_response = get_agent_response(prompt, current_data, st.session_state.messages[:-1])
                
                action_url = None
                action_match = re.search(r'\[ACTION_OPEN_URL:\s*(http[^\s\]]+)\]', raw_response)
                
                if action_match:
                    action_url = action_match.group(1)
                    clean_response = raw_response.replace(action_match.group(0), "").strip()
                else:
                    clean_response = raw_response
                
                st.markdown(clean_response)
                st.session_state.messages.append({"role": "assistant", "content": clean_response})
                
                if action_url:
                    js_code = f"window.open('{action_url}', '_blank');"
                    components.html(f"<script>{js_code}</script>", height=0)
                    st.success(f"Agent autonomously opened: {action_url}")

# --- 4. Main Dashboard UI ---
st.title("🤖 Agent Command Center")
st.caption("Strategic analysis of your recurring expenses and service utility.")

data = fetch_subscription_data()

if data is None:
    st.error("🚨 Server unreachable. Ensure 'server.py' is running on localhost:8000.")
else:
    df = pd.DataFrame(data)
    
    waste_df = df[df['agent_recommendation'].str.contains('cancel|Expensive|Review', case=False, na=False)]
    keep_df = df[df['agent_recommendation'].str.contains('Keep', case=False, na=False)]
    
    total_monthly = df['monthly_cost'].sum()
    identified_waste = waste_df['monthly_cost'].sum()

    m_col1, m_col2, m_col3 = st.columns(3)
    m_col1.metric("Total Monthly Spend", f"₪ {total_monthly:,.2f}")
    m_col2.metric("Identified Waste", f"₪ {identified_waste:,.2f}", delta_color="inverse")
    m_col3.metric("Active Subscriptions", len(df))

    st.divider()

    st.subheader("🎯 Agent Action Center")
    
    # --- SECTION A: Needs Attention ---
    st.markdown("#### 🚨 Needs Attention")
    critical_subs = waste_df.to_dict('records')
    
    if not critical_subs:
        st.success("Your subscriptions are optimized! No wasteful items found.")
    else:
        cols = st.columns(2) 
        for idx, sub in enumerate(critical_subs):
            with cols[idx % 2]:
                with st.container(border=True):
                    st.write(f"### {sub['service_name']}")
                    st.write(f"**Cost:** ₪ {sub['monthly_cost']} | **Usage:** {sub['current_period_usage_hours']}h/mo")
                    st.warning(f"**AI Insight:** {sub['agent_recommendation']}")
                    
                    # 3-Column Layout for the Actions
                    c1, c2, c3 = st.columns(3)
                    url = sub.get('unsubscribe_url')
                    
                    if url and url != "NULL":
                        c1.link_button("Cancel Service", url, type="primary", use_container_width=True)
                    else:
                        if c1.button("Cancel Service", key=f"src_{sub['subscription_id']}", type="primary", use_container_width=True):
                            with st.spinner("Agent locating link..."):
                                found_url = get_cancellation_url(sub['service_name'])
                                if found_url:
                                    js_code = f"window.open('{found_url}', '_blank');"
                                    components.html(f"<script>{js_code}</script>", height=0)
                                    st.success("Found it!")
                                else:
                                    st.error("No link found.")
                    
                    # The Negotiator Button
                    if c2.button("🤝 Negotiate", key=f"neg_{sub['subscription_id']}", use_container_width=True):
                        with st.spinner("Drafting email..."):
                            draft = draft_negotiation_email(sub['service_name'], sub['monthly_cost'], sub['current_period_usage_hours'])
                            if draft:
                                # Safely encode the text for a URL
                                subject = urllib.parse.quote(draft['subject'])
                                body = urllib.parse.quote(draft['body'])
                                mailto_url = f"mailto:{draft['support_email']}?subject={subject}&body={body}"
                                
                                # Use _self to trigger the default mail client without leaving a blank browser tab
                                js_code = f"window.open('{mailto_url}', '_self');"
                                components.html(f"<script>{js_code}</script>", height=0)
                                st.success("Draft created! Check your email app.")
                                
                    c3.button("Dismiss", key=f"kp_{sub['subscription_id']}", use_container_width=True)

    st.divider()

    # --- SECTION B: Optimized & Kept ---
    st.markdown("#### ✅ Optimized & Active")
    kept_subs = keep_df.to_dict('records')
    
    if not kept_subs:
        st.info("No active subscriptions are currently marked to keep.")
    else:
        cols2 = st.columns(2)
        for idx, sub in enumerate(kept_subs):
            with cols2[idx % 2]:
                with st.container(border=True):
                    st.write(f"### {sub['service_name']}")
                    st.write(f"**Cost:** ₪ {sub['monthly_cost']} | **Usage:** {sub['current_period_usage_hours']}h/mo")
                    st.success(f"**AI Insight:** {sub['agent_recommendation']}")
                    
                    # Add Negotiator to Kept Items as well
                    if st.button("🤝 Negotiate Rate", key=f"neg_keep_{sub['subscription_id']}", use_container_width=True):
                        with st.spinner("Drafting email..."):
                            draft = draft_negotiation_email(sub['service_name'], sub['monthly_cost'], sub['current_period_usage_hours'])
                            if draft:
                                subject = urllib.parse.quote(draft['subject'])
                                body = urllib.parse.quote(draft['body'])
                                mailto_url = f"mailto:{draft['support_email']}?subject={subject}&body={body}"
                                
                                js_code = f"window.open('{mailto_url}', '_self');"
                                components.html(f"<script>{js_code}</script>", height=0)
                                st.success("Draft created! Check your email app.")

    st.divider()

    st.subheader("📂 Subscription Data Vault")
    st.dataframe(df[['service_name', 'category', 'monthly_cost', 'current_period_usage_hours', 'agent_recommendation']], use_container_width=True)