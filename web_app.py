import streamlit as st
import requests
import json
import re
from google import genai

# --- 1. Configuration ---
st.set_page_config(page_title="MATE-GBI Gene Explorer", layout="wide")

# Explicit Server Mapping
SERVERS = {
    "Vertebrates (Ensembl Main)": "https://rest.ensembl.org",
    "Plants (Ensembl Genomes)": "https://rest.ensemblgenomes.org",
    "Bacteria (Ensembl Genomes)": "https://rest.ensemblgenomes.org",
    "Fungi (Ensembl Genomes)": "https://rest.ensemblgenomes.org",
    "Protists/Metazoa": "https://rest.ensemblgenomes.org"
}

HEADERS = {"Content-Type": "application/json", "Accept": "application/json"}
MODEL_NAME = "gemini-2.5-flash"

# --- 2. Professional Logic Functions ---

def fetch_api(base_url, endpoint):
    """Core communication with the selected REST API."""
    try:
        url = f"{base_url}{endpoint}"
        response = requests.get(url, headers=HEADERS, timeout=15)
        if response.status_code == 200:
            return response.json()
    except:
        pass
    return None

def resolve_strain(base_url, species_query):
    """
    Finds the official 'slug' within the selected server.
    Fixes broad names like 'escherichia_coli' to 'escherichia_coli_str_k_12_substr_mg1655'.
    """
    res = fetch_api(base_url, f"/info/species?name={species_query}")
    if res and "species" in res and len(res["species"]) > 0:
        return res["species"][0]["name"]
    return species_query

def smart_lookup(base_url, species, query):
    """
    Searches the selected server using 3 steps:
    1. Direct Lookup (ID or Symbol)
    2. Xref Fallback (Searching external references for synonyms)
    """
    if not query: return None
    
    # Clean species for the specific server
    official_sp = resolve_strain(base_url, species)
    
    # Step A: Direct Lookup (Handles IDs and standard Symbols)
    # Check if input looks like an Ensembl or Plant ID
    is_id = bool(re.match(r"^(ENS|Traes|Os|AT|MLOC|HORVU|Zm|Sobic|b\d{4}).*", query, re.I))
    
    if is_id:
        data = fetch_api(base_url, f"/lookup/id/{query}")
        if data: return data

    # Try Symbol lookup
    data = fetch_api(base_url, f"/lookup/symbol/{official_sp}/{query}")
    if data: return data

    # Step B: Deep Xref Search (Essential for common names like Rht-B1 or lacZ)
    xrefs = fetch_api(base_url, f"/xrefs/symbol/{official_sp}/{query}")
    if xrefs:
        for item in xrefs:
            if item.get("type") == "gene":
                # Return the full lookup of the linked Ensembl ID
                return fetch_api(base_url, f"/lookup/id/{item.get('id')}")
    return None

def call_gemini(api_key, prompt):
    """Gemini 2.0 Flash Synthesis."""
    try:
        client = genai.Client(api_key=api_key)
        response = client.models.generate_content(model=MODEL_NAME, contents=prompt)
        return response.text.strip()
    except Exception as e:
        return f"⚠️ AI Error: {str(e)}"

# --- 3. Sidebar (Branding & Global Settings) ---

with st.sidebar:
    try:
        st.image("mate_logo.png", use_container_width=True)
    except:
        st.title("MATE-GBI")
    
    st.markdown("### 🖥️ Server Selection")
    # THE USER FORCES THE SERVER HERE
    selected_division = st.selectbox("Choose Division:", list(SERVERS.keys()))
    base_url = SERVERS[selected_division]

    st.markdown("---")
    try:
        api_key = st.secrets["GEMINI_API_KEY"]
        st.success("🤖 AI Engine Active")
    except:
        api_key = st.text_input("Gemini API Key:", type="password")

    ai_enabled = st.checkbox("Enable AI Analysis", value=True)
    
    st.markdown("---")
    st.caption("Designed by Maher, MATE-GBI")
    st.caption("Hungarian University of Agriculture and Life Sciences")

# --- 4. Main Interface ---

st.title("🧬 Gene Explorer")
st.info(f"Targeting: **{selected_division}**")

# Global Species (Applies to both tabs)
species_input = st.text_input("Species Name (e.g. triticum_aestivum or escherichia_coli):", value="homo_sapiens")

tab1, tab2 = st.tabs(["🔍 Single Lookup", "🔗 Relationship Analysis"])

# --- TAB 1: SINGLE LOOKUP ---
with tab1:
    c1, c2 = st.columns([1, 2])
    with c1:
        query_input = st.text_input("Gene Name or ID:", placeholder="e.g. Rht-B1, lacZ, BRCA2")
        search_btn = st.button("Fetch Metadata", use_container_width=True)

    if search_btn and query_input:
        with st.spinner("Searching specific database..."):
            data = smart_lookup(base_url, species_input, query_input)
        
        if data:
            st.success(f"✅ Gene Found in {data.get('species')}")
            st.markdown(f"### {data.get('display_name', query_input)}")
            st.write(f"**Description:** {data.get('description', 'No description found.')}")
            
            res_c1, res_c2 = st.columns(2)
            with res_c1:
                st.write(f"**Stable ID:** `{data.get('id')}`")
                st.write(f"**Biotype:** {data.get('biotype')}")
            with res_c2:
                st.write(f"**Location:** {data.get('seq_region_name')}:{data.get('start')}-{data.get('end')}")
                st.write(f"**Assembly:** {data.get('assembly_name')}")

            if ai_enabled and api_key:
                with st.spinner("AI Synthesis..."):
                    prompt = f"Provide a concise, scientific 3-bullet summary for this gene metadata: {json.dumps(data)}"
                    st.info(f"**🤖 AI Insights:**\n\n{call_gemini(api_key, prompt)}")
            
            with st.expander("Show Raw Metadata"):
                st.json(data)
        else:
            st.error(f"❌ '{query_input}' not found on the **{selected_division}** server.")
            st.warning("Double-check that your Server Selection (Sidebar) matches your organism.")

# --- TAB 2: RELATIONSHIP ANALYSIS ---
with tab2:
    st.subheader("Concise Multi-Gene Relationship Analysis")
    gene_list_raw = st.text_area("Gene List (separated by commas):", placeholder="TP53, BRCA1, ATM")
    analyze_btn = st.button("Analyze Connections", use_container_width=True)

    if analyze_btn and gene_list_raw:
        if not api_key:
            st.error("API Key Required.")
        else:
            genes = [g.strip() for g in gene_list_raw.replace("\n", ",").split(",") if g.strip()]
            collected_data = []
            
            pbar = st.progress(0)
            status = st.empty()
            
            for i, g in enumerate(genes):
                status.text(f"Fetching {g}...")
                d = smart_lookup(base_url, species_input, g)
                if d:
                    collected_data.append({"symbol": d.get('display_name'), "desc": d.get('description')})
                pbar.progress((i + 1) / len(genes))
            
            status.empty()
            
            if collected_data:
                with st.spinner("AI Analysis..."):
                    # Strict Concise Prompt
                    prompt = f"""
                    Analyze the biological relationships between these genes: {json.dumps(collected_data)}. 
                    Requirements:
                    - Concise, bulleted scientific synthesis. 
                    - Focus strictly on shared pathways and functional connections.
                    - Straight to the point. No introductory or closing text.
                    """
                    analysis = call_gemini(api_key, prompt)
                    st.markdown("### --- CONCISE RELATIONSHIP ANALYSIS ---")
                    st.markdown(analysis)
            else:
                st.error("Could not retrieve data for any genes on the current server.")