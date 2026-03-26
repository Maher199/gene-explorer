import streamlit as st
import requests
import json
import re
import time
from google import genai

# --- 1. Global Configuration ---
st.set_page_config(page_title="MATE-GBI Universal Gene Explorer", layout="wide")

SERVERS = {
    "Vertebrates (Ensembl)": "https://rest.ensembl.org",
    "Bacteria/Plants/Fungi": "https://rest.ensemblgenomes.org"
}
HEADERS = {"Content-Type": "application/json", "Accept": "application/json"}
ENSEMBL_ID_PATTERN = re.compile(r"^ENS[A-Z]*[GTPR]\d+(?:\.\d+)?$")
# Pattern to recognize common plant locus formats
PLANT_ID_PATTERN = re.compile(r"^(Os|Traes|AT|MLOC|HORVU|Zm|Sobic).*", re.IGNORECASE)

MODEL_NAME = "gemini-2.5-flash"

# --- 2. Helper Logic ---

def fetch_json(base_url, endpoint):
    """Basic REST call wrapper."""
    try:
        response = requests.get(f"{base_url}{endpoint}", headers=HEADERS, timeout=15)
        if response.status_code == 200:
            return response.json()
        return None
    except Exception:
        return None

def smart_lookup(base_url, species, query):
    """
    Tries 3 ways to find a gene:
    1. Direct Ensembl ID lookup
    2. Direct Symbol lookup
    3. Xref (External Reference) lookup - essential for Wheat/Rice common names
    """
    if not query: return None
    
    # Try 1: ID lookup (if it looks like an ID)
    if ENSEMBL_ID_PATTERN.match(query) or PLANT_ID_PATTERN.match(query):
        res = fetch_json(base_url, f"/lookup/id/{query}")
        if res: return res

    # Try 2: Standard Symbol lookup
    res = fetch_json(base_url, f"/lookup/symbol/{species}/{query}")
    if res: return res

    # Try 3: Xref Fallback (Searching for common names in External Refs)
    xrefs = fetch_json(base_url, f"/xrefs/symbol/{species}/{query}")
    if xrefs and len(xrefs) > 0:
        # Get the first valid Ensembl ID from the Xref list
        target_id = xrefs[0].get("id")
        if target_id:
            return fetch_json(base_url, f"/lookup/id/{target_id}")
            
    return None

def call_gemini(api_key, prompt):
    """AI call with basic error handling."""
    try:
        client = genai.Client(api_key=api_key)
        response = client.models.generate_content(model=MODEL_NAME, contents=prompt)
        return response.text.strip()
    except Exception as e:
        if "429" in str(e):
            return "⚠️ AI Rate Limit Hit. Please wait a minute and try again."
        return f"❌ AI Error: {str(e)}"

# --- 3. UI Sidebar ---

with st.sidebar:
    try:
        st.image("mate_logo.png", use_container_width=True)
    except:
        st.title("MATE-GBI")
    
    st.markdown("---")
    db_choice = st.selectbox("Database Server:", list(SERVERS.keys()))
    
    # Secure API Key handling
    try:
        api_key = st.secrets["GEMINI_API_KEY"]
        st.success("🤖 AI Connected (Secrets)")
    except:
        api_key = st.text_input("Gemini API Key:", type="password", help="Enter key if not set in Secrets.")

    ai_enabled = st.checkbox("Enable AI Summary", value=True)
    
    st.markdown("---")
    st.caption("Designed by Maher, MATE-GBI")
    st.caption("Hungarian University of Agriculture and Life Sciences")

# --- 4. Main UI Tabs ---

st.title("Universal Gene Explorer")
tab1, tab2 = st.tabs(["🧬 Single Gene Lookup", "🔗 Relationship Analysis"])

# --- TAB 1: SINGLE LOOKUP ---
with tab1:
    c1, c2 = st.columns([1, 2])
    with c1:
        species_input = st.text_input("Species Name:", value="homo_sapiens", help="e.g. triticum_aestivum, zea_mays")
        query_input = st.text_input("Gene Name or ID:", placeholder="e.g. Rht-B1 or BRCA2")
        search_btn = st.button("Search")

    if search_btn and query_input:
        base_url = SERVERS[db_choice]
        with st.spinner(f"Searching for {query_input}..."):
            data = smart_lookup(base_url, species_input, query_input)
        
        if data:
            st.success(f"Gene Found: {data.get('display_name', query_input)}")
            
            # Display Core Metadata
            md_col1, md_col2 = st.columns(2)
            with md_col1:
                st.write(f"**Stable ID:** `{data.get('id')}`")
                st.write(f"**Species:** {data.get('species')}")
                st.write(f"**Biotype:** {data.get('biotype')}")
            with md_col2:
                st.write(f"**Chromosome:** {data.get('seq_region_name')}")
                st.write(f"**Coordinates:** {data.get('start')}-{data.get('end')}")
                st.write(f"**Assembly:** {data.get('assembly_name')}")

            st.write(f"**Description:** {data.get('description', 'No description available in Ensembl.')}")

            # AI Summary
            if ai_enabled and api_key:
                st.markdown("---")
                with st.spinner("AI Summarizing..."):
                    prompt = f"Provide a concise, scientific 3-bullet point summary for this gene metadata: {json.dumps(data)}"
                    summary = call_gemini(api_key, prompt)
                    st.markdown("#### 🤖 AI Summary")
                    st.write(summary)
            
            with st.expander("Show Raw API Metadata"):
                st.json(data)
        else:
            st.error(f"❌ '{query_input}' not found. Tips: For Wheat use 'Rht-B1', for Rice use 'SD1', and ensure 'Bacteria/Plants/Fungi' server is selected.")

# --- TAB 2: RELATIONSHIP ANALYSIS ---
with tab2:
    st.subheader("Concise Multi-Gene Relationship Analysis")
    st.write("Paste a list of genes. The AI will look them all up and find functional connections.")
    
    gene_list_raw = st.text_area("Gene List (separated by commas):", placeholder="TP53, BRCA1, ATM, CHEK2")
    analyze_btn = st.button("Analyze Relationship")

    if analyze_btn and gene_list_raw:
        if not api_key:
            st.error("API Key required for Relationship Analysis.")
        else:
            genes = [g.strip() for g in gene_list_raw.replace("\n", ",").split(",") if g.strip()]
            base_url = SERVERS[db_choice]
            collected_data = []
            
            pbar = st.progress(0)
            status_text = st.empty()
            
            for i, g in enumerate(genes):
                status_text.text(f"Fetching {g}...")
                d = smart_lookup(base_url, species_input, g)
                if d:
                    collected_data.append({"symbol": d.get('display_name'), "desc": d.get('description')})
                pbar.progress((i + 1) / len(genes))
            
            status_text.empty()
            
            if collected_data:
                st.info(f"Retrieved data for {len(collected_data)} genes. Analyzing...")
                with st.spinner("AI analyzing connections..."):
                    # Strict Concise Prompt
                    prompt = f"""
                    Analyze the biological relationships between these genes: {json.dumps(collected_data)}. 
                    Requirements:
                    - Concise, bulleted scientific synthesis. 
                    - Focus strictly on shared pathways, interactions, or common functions.
                    - Straight to the point. No introductory or closing text.
                    """
                    analysis = call_gemini(api_key, prompt)
                    st.markdown("### --- CONCISE RELATIONSHIP ANALYSIS ---")
                    st.markdown(analysis)
            else:
                st.error("Could not retrieve data for any of the genes provided.")