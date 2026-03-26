import streamlit as st
import requests
import json
import re
from google import genai

# --- Configuration ---
st.set_page_config(page_title="MATE-GBI Power Explorer", layout="wide")

SERVERS = {
    "Vertebrates (Ensembl)": "https://rest.ensembl.org",
    "Bacteria/Plants/Fungi": "https://rest.ensemblgenomes.org"
}
HEADERS = {"Content-Type": "application/json", "Accept": "application/json"}
MODEL_NAME = "gemini-2.5-flash"

# --- Power Search Logic ---

def fetch_api(base_url, endpoint):
    """Silent API fetcher."""
    try:
        url = f"{base_url}{endpoint}"
        response = requests.get(url, headers=HEADERS, timeout=15)
        if response.status_code == 200:
            return response.json()
        return None
    except:
        return None

def power_lookup(base_url, species, query):
    """
    Exhaustive search for tricky plant genes like Rht-B1.
    """
    if not query: return None

    # 1. Try Direct Lookup (Symbol/ID)
    # Most common way for humans
    res = fetch_api(base_url, f"/lookup/symbol/{species}/{query}")
    if res: return res
    
    # 2. Try ID Lookup (If user pasted a TraesCS... ID)
    res = fetch_api(base_url, f"/lookup/id/{query}")
    if res: return res

    # 3. Try Xref Symbol Search (Crucial for Wheat/Rice Synonyms)
    # This checks external databases linked to Ensembl
    xrefs = fetch_api(base_url, f"/xrefs/symbol/{species}/{query}")
    if xrefs:
        # Sort to find the best Ensembl Gene match (starts with 'Traes' or 'ENS')
        for item in xrefs:
            if item.get("type") == "gene":
                target_id = item.get("id")
                return fetch_api(base_url, f"/lookup/id/{target_id}")

    # 4. Try Map Search (If it's an old symbol)
    # Some genes are only found via the 'homology' or 'xrefs' endpoints
    return None

def call_gemini(api_key, prompt):
    try:
        client = genai.Client(api_key=api_key)
        response = client.models.generate_content(model=MODEL_NAME, contents=prompt)
        return response.text.strip()
    except Exception as e:
        return f"⚠️ AI Error: {str(e)}"

# --- UI Sidebar ---
with st.sidebar:
    try:
        st.image("mate_logo.png", use_container_width=True)
    except:
        st.title("MATE-GBI")
    
    st.markdown("### ⚙️ Settings")
    db_choice = st.selectbox("Database Server:", list(SERVERS.keys()))
    
    try:
        api_key = st.secrets["GEMINI_API_KEY"]
        st.success("🤖 AI Ready")
    except:
        api_key = st.text_input("Gemini API Key:", type="password")

    ai_enabled = st.checkbox("Enable AI Analysis", value=True)
    st.markdown("---")
    st.caption("Designed by Maher, MATE-GBI")

# --- UI Main ---
st.title("🧬 Universal Gene Explorer")
st.markdown("---")

col1, col2 = st.columns([1, 2])

with col1:
    species = st.text_input("Species (must be lowercase_underscore):", value="triticum_aestivum")
    query = st.text_input("Gene Name (Try: Rht-B1 or TraesCS4B02G043100):")
    search_btn = st.button("Deep Search", use_container_width=True)

with col2:
    if search_btn and query:
        base_url = SERVERS[db_choice]
        with st.spinner(f"Running Deep Search for '{query}'..."):
            data = power_lookup(base_url, species, query)
        
        if data:
            st.success(f"✅ Found: {data.get('display_name', query)}")
            
            # Formatted Output
            st.markdown(f"**Description:** {data.get('description', 'N/A')}")
            
            res_col1, res_col2 = st.columns(2)
            with res_col1:
                st.write(f"**Ensembl ID:** `{data.get('id')}`")
                st.write(f"**Biotype:** {data.get('biotype')}")
            with res_col2:
                st.write(f"**Location:** Chr {data.get('seq_region_name')}")
                st.write(f"**Coordinates:** {data.get('start')}-{data.get('end')}")

            if ai_enabled and api_key:
                with st.spinner("AI Synthesis..."):
                    prompt = f"Concise 3-bullet summary of this gene metadata: {json.dumps(data)}"
                    summary = call_gemini(api_key, prompt)
                    st.info(f"**🤖 AI Analysis:**\n\n{summary}")
            
            with st.expander("View Raw Data"):
                st.json(data)
        else:
            st.error("❌ Gene not found.")
            st.warning(f"Note: Ensure you are on the '{db_choice}' server. Some plant names are only found using official IDs (e.g. TraesCS4B02G043100).")