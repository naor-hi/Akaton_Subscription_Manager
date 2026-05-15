import streamlit as st
import pandas as pd
import os
import json
import streamlit.components.v1 as components
from openai import OpenAI
from agent_client import AgentClient
from datetime import datetime
from dotenv import load_dotenv

# --- 1. Setup & Style ---
load_dotenv()
st.set_page_config(page_title="Agent Command Center", page_icon="🤖", layout="wide")

# Custom CSS to fix the input box to the bottom and style the sidebar
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
    /* Pins the chat input to the bottom of the sidebar */
    div[data-testid="stForm"] {
        border: none;
        padding: 0;
    }
    </style>
    """, unsafe_allow_html=True)

# Initialize OpenRouter Client
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
    """Agentic function that asks Gemini ONLY for a direct URL."""
    prompt = f"Provide ONLY the direct cancellation URL for {service_name}. Do not include any other text, formatting, or markdown. Just the raw https:// link."
    
    try:
        response = llm_client.chat.completions.create(
            model="google/gemini-2.5-flash",
            messages=[{"role": "user", "content": prompt}],
            temperature=0.1 # Keep it strictly factual
        )
        url = response.choices[0].message.content.strip()
        if url.startswith("http"):
            return url
    except Exception as e:
        return None
    return None

def get_agent_response(user_input, context_data, history):
    """Commands Gemini 2.5 Flash to act as a high-tier financial assistant."""
    system_prompt = f"""
    You are the Akaton Financial Agent (Gemini 2.5 Flash). 
    You are an expert in SaaS, digital subscriptions, and web navigation.

    CORE COMMANDS:
    1. WEB ACCESS: Use your internal knowledge to provide DIRECT URLs for cancellation or signup.
    2. COMPARISONS: If asked for alternatives (VPNs, Music, AI tools), provide a detailed breakdown.
    3. CONTEXT: You have the user's specific subscription and usage data below.
    
    USER DATA:
    {json.dumps(context_data, indent=2)}
    
    Tone: Professional, direct, and efficient.
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
    st.caption("Gemini 2.5 Flash | Financial Analysis")
    
    # Scrollable container for chat
    chat_container = st.container(height=550)
    
    if "messages" not in st.session_state:
        st.session_state.messages = []

    with chat_container:
        for message in st.session_state.messages:
            with st.chat_message(message["role"]):
                st.markdown(message["content"])

    # Chat Input pinned to bottom
    if prompt := st.chat_input("Ask for a link, comparison, or insight..."):
        st.session_state.messages.append({"role": "user", "content": prompt})
        with chat_container:
            with st.chat_message("user"):
                st.markdown(prompt)

            with st.chat_message("assistant"):
                current_data = fetch_subscription_data()
                response = get_agent_response(prompt, current_data, st.session_state.messages[:-1])
                st.markdown(response)
                st.session_state.messages.append({"role": "assistant", "content": response})

# --- 4. Main Dashboard UI ---
st.title("🤖 Agent Command Center")
st.caption("Strategic analysis of your recurring expenses and service utility.")

data = fetch_subscription_data()

if data is None:
    st.error("🚨 Server unreachable. Ensure 'server.py' is running on localhost:8000.")
else:
    df = pd.DataFrame(data)
    
    # --- Metrics ---
    total_monthly = df['monthly_cost'].sum()
    # Filter for wasteful items
    waste_df = df[df['agent_recommendation'].str.contains('cancel|Expensive|Review', case=False, na=False)]
    identified_waste = waste_df['monthly_cost'].sum()

    m_col1, m_col2, m_col3 = st.columns(3)
    m_col1.metric("Total Monthly Spend", f"₪ {total_monthly:,.2f}")
    m_col2.metric("Identified Waste", f"₪ {identified_waste:,.2f}", delta_color="inverse")
    m_col3.metric("Active Subscriptions", len(df))

    st.divider()

    # --- Action Center ---
    st.subheader("🎯 Agent Action Center")
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
                    
                    # CANCELLATION LOGIC
                    c1, c2 = st.columns(2)
                    url = sub.get('unsubscribe_url')
                    
                    # If we already have the URL in the database
                    if url and url != "NULL":
                        c1.link_button("Cancel Subscription", url, type="primary", use_container_width=True)
                    else:
                        # The Agentic Button: Finds the link and forces a redirect
                        if c1.button("Cancel Subscription", key=f"src_{sub['subscription_id']}", type="primary", use_container_width=True):
                            with st.spinner("Agent locating cancel page..."):
                                found_url = get_cancellation_url(sub['service_name'])
                                
                                if found_url:
                                    # Inject JavaScript to instantly pop open the new tab
                                    js_code = f"window.open('{found_url}', '_blank');"
                                    components.html(f"<script>{js_code}</script>", height=0)
                                    
                                    # Fallback button just in case the user's browser blocks pop-ups
                                    st.success("Found it!")
                                    st.link_button("Click here if tab didn't open", found_url, use_container_width=True)
                                else:
                                    st.error("Agent could not find a direct link.")
                    
                    c2.button("Dismiss", key=f"kp_{sub['subscription_id']}", use_container_width=True)

    st.divider()

    # --- Data Vault ---
    st.subheader("📂 Subscription Data Vault")
    st.dataframe(df[['service_name', 'category', 'monthly_cost', 'current_period_usage_hours', 'agent_recommendation']], use_container_width=True)