import streamlit as st
import requests
import json
import re
from google import genai

# --- Configuration ---
st.set_page_config(page_title="MATE-GBI Gene Explorer", layout="wide")

SERVERS = {
    "Vertebrates (Ensembl)": "https://rest.ensembl.org",
    "Bacteria/Plants/Fungi": "https://rest.ensemblgenomes.org"
}
HEADERS = {"Content-Type": "application/json", "Accept": "application/json"}
ENSEMBL_ID_PATTERN = re.compile(r"^ENS[A-Z]*[GTPR]\d+(?:\.\d+)?$")
MODEL_NAME = "gemini-2.5-flash"

# --- Helper Functions ---
def fetch_json(base_url, endpoint):
    try:
        response = requests.get(f"{base_url}{endpoint}", headers=HEADERS, timeout=15)
        if not response.ok: return None
        return response.json()
    except: return None

def call_gemini(api_key, prompt):
    try:
        client = genai.Client(api_key=api_key)
        response = client.models.generate_content(model=MODEL_NAME, contents=prompt)
        return response.text.strip()
    except Exception as e:
        return f"⚠️ AI Error: {str(e)}"

# --- UI Sidebar ---
with st.sidebar:
    # Try to load local logo, otherwise show text
    try:
        st.image("mate_logo.png", use_container_width=True)
    except:
        st.title("MATE-GBI")
    
    st.markdown("---")
    db_choice = st.selectbox("Database Server:", list(SERVERS.keys()))

    # --- THE SECRETS PART ---
    try:
        # This line fetches the key from the hidden settings
        api_key = st.secrets["GEMINI_API_KEY"]
        st.success("🤖 AI Engine Connected")
    except:
        st.error("⚠️ API Key not found in Secrets.")
        api_key = None
    # ------------------------


    ai_enabled = st.checkbox("Enable AI Analysis", value=True)
    
    st.markdown("---")
    st.caption("Designed by Maher, MATE-GBI")

# --- Main Interface ---
st.title("Universal Gene Explorer")
st.info("Search vertebrates, bacteria, plants, and fungi using the Ensembl REST API.")

tab1, tab2 = st.tabs(["🧬 Single Gene Lookup", "🔗 Relationship Analysis"])

# --- Tab 1: Single Lookup ---
with tab1:
    col1, col2 = st.columns([1, 2])
    with col1:
        species = st.text_input("Species Name:", value="homo_sapiens", help="e.g. escherichia_coli, triticum_aestivum")
        query = st.text_input("Gene Symbol or Ensembl ID:", placeholder="e.g. BRCA2")
        search_btn = st.button("Search Gene")

    if search_btn and query:
        base_url = SERVERS[db_choice]
        is_id = bool(ENSEMBL_ID_PATTERN.match(query))
        endpoint = f"/lookup/id/{query}" if is_id else f"/lookup/symbol/{species}/{query}"
        
        with st.spinner("Fetching data..."):
            data = fetch_json(base_url, endpoint)
        
        if data:
            st.subheader(f"Results for {data.get('display_name', query)}")
            st.json(data) # Show raw data in an expandable format
            
            if ai_enabled and api_key:
                st.markdown("### 🤖 AI Summary")
                prompt = f"Provide a concise 3-bullet scientific summary of this gene: {json.dumps(data)}"
                summary = call_gemini(api_key, prompt)
                st.write(summary)
        else:
            st.error("Gene not found. Check your species name and server selection.")

# --- Tab 2: Relationship Analysis ---
with tab2:
    st.subheader("Concise Relationship Analysis")
    gene_list_raw = st.text_area("Enter gene list (comma separated):", placeholder="TP53, BRCA1, ATM, CHEK2")
    analyze_btn = st.button("Analyze Connections")

    if analyze_btn and gene_list_raw:
        if not api_key:
            st.warning("Please enter an API Key in the sidebar to use AI analysis.")
        else:
            genes = [g.strip() for g in gene_list_raw.replace("\n", ",").split(",") if g.strip()]
            base_url = SERVERS[db_choice]
            collected = []
            
            progress_bar = st.progress(0)
            for i, g in enumerate(genes):
                is_id = bool(ENSEMBL_ID_PATTERN.match(g))
                endpoint = f"/lookup/id/{g}" if is_id else f"/lookup/symbol/{species}/{g}"
                d = fetch_json(base_url, endpoint)
                if d: collected.append({"symbol": d.get('display_name'), "desc": d.get('description')})
                progress_bar.progress((i + 1) / len(genes))
            
            if collected:
                st.success(f"Retrieved data for {len(collected)} genes.")
                with st.spinner("Analyzing relationships..."):
                    prompt = f"""Analyze the biological relationship between these genes: {json.dumps(collected)}. 
                    Requirement: Provide a concise, bulleted scientific synthesis. 
                    Focus strictly on shared pathways and functional co-expression. 
                    Be straight to the point without introductions."""
                    analysis = call_gemini(api_key, prompt)
                    st.markdown("### --- CONCISE ANALYSIS ---")
                    st.write(analysis)
            else:
                st.error("Could not find data for those genes.")
