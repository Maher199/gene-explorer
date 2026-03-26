import streamlit as st
import requests
import json
import re
from google import genai

# --- 1. Configuration ---
st.set_page_config(page_title="MATE-GBI Universal Gene Explorer", layout="wide")

# Servers to scan automatically
SERVERS = [
    "https://rest.ensembl.org",        
    "https://rest.ensemblgenomes.org"  
]

HEADERS = {"Content-Type": "application/json", "Accept": "application/json"}
MODEL_NAME = "gemini-2.5-flash"

# --- 2. Advanced Search & Species Logic ---

def fetch_api(base_url, endpoint):
    """Silent API fetcher with error handling."""
    try:
        url = f"{base_url}{endpoint}"
        response = requests.get(url, headers=HEADERS, timeout=15)
        if response.status_code == 200:
            return response.json()
    except:
        pass
    return None

def resolve_species(query_species):
    """
    Finds the most complete 'Official Slug' for a species name.
    Fixes broad names like 'escherichia_coli' or 'wheat'.
    """
    for base_url in SERVERS:
        res = fetch_api(base_url, f"/info/species?name={query_species}")
        if res and "species" in res and len(res["species"]) > 0:
            return res["species"][0]["name"]
    return query_species

def hyper_search(species, query):
    """
    Scans IDs, Symbols, and Xrefs across all Ensembl Servers.
    """
    if not query: return None
    
    # Resolve strain for bacteria/plants
    official_species = resolve_species(species)
    
    # Check if ID (ENS..., Traes..., Os..., b0344...)
    is_id = bool(re.match(r"^(ENS|Traes|Os|AT|MLOC|HORVU|Zm|Sobic|b\d{4}).*", query, re.I))

    for base_url in SERVERS:
        # Step 1: Lookup by ID
        if is_id:
            data = fetch_api(base_url, f"/lookup/id/{query}")
            if data: return data

        # Step 2: Lookup by Symbol
        data = fetch_api(base_url, f"/lookup/symbol/{official_species}/{query}")
        if data: return data

        # Step 3: Deep Xref Search (For Rht-B1, SD1, etc.)
        xrefs = fetch_api(base_url, f"/xrefs/symbol/{official_species}/{query}")
        if xrefs:
            for item in xrefs:
                if item.get("type") == "gene":
                    data = fetch_api(base_url, f"/lookup/id/{item.get('id')}")
                    if data: return data
    return None

def call_gemini(api_key, prompt):
    """AI Synthesis."""
    try:
        client = genai.Client(api_key=api_key)
        response = client.models.generate_content(model=MODEL_NAME, contents=prompt)
        return response.text.strip()
    except Exception as e:
        return f"⚠️ AI Error: {str(e)}"

# --- 3. Sidebar UI ---
with st.sidebar:
    try:
        st.image("mate_logo.png", use_container_width=True)
    except:
        st.title("MATE-GBI")
    
    st.markdown("### 🔑 API Key")
    try:
        api_key = st.secrets["GEMINI_API_KEY"]
        st.success("🤖 AI Engine Active")
    except:
        api_key = st.text_input("Gemini API Key:", type="password")

    ai_enabled = st.checkbox("Enable AI Synthesis", value=True)
    st.markdown("---")
    st.caption("Designed by Maher, MATE-GBI")
    st.caption("Hungarian University of Agriculture and Life Sciences")

# --- 4. Main UI ---
st.title("🧬 Universal Gene Explorer")
st.info("Searching Bacteria, Plants, Fungi, and Vertebrates across all Ensembl divisions.")

# Global Species Input (Used by both tabs)
species_input = st.text_input("Species Name (lowercase_underscore):", value="homo_sapiens", help="e.g. triticum_aestivum, escherichia_coli, oryza_sativa")

tab1, tab2 = st.tabs(["🔍 Single Gene Lookup", "🔗 Relationship Analysis"])

# --- TAB 1: SINGLE LOOKUP ---
with tab1:
    col1, col2 = st.columns([1, 2])
    with col1:
        query_input = st.text_input("Gene ID or Symbol:", placeholder="e.g. Rht-B1, lacZ, or BRCA2")
        search_btn = st.button("Deep Search", use_container_width=True)

    if search_btn and query_input:
        with st.spinner(f"Scanning Ensembl for '{query_input}'..."):
            data = hyper_search(species_input, query_input)
        
        if data:
            st.success(f"✅ Found in: {data.get('species')}")
            st.markdown(f"### {data.get('display_name', query_input)}")
            st.markdown(f"**Description:** {data.get('description', 'N/A')}")
            
            res_c1, res_c2 = st.columns(2)
            with res_c1:
                st.write(f"**Stable ID:** `{data.get('id')}`")
                st.write(f"**Biotype:** {data.get('biotype')}")
            with res_c2:
                st.write(f"**Location:** {data.get('seq_region_name')}:{data.get('start')}-{data.get('end')}")
                st.write(f"**Assembly:** {data.get('assembly_name')}")

            if ai_enabled and api_key:
                with st.spinner("AI Generating Summary..."):
                    prompt = f"Provide a concise, scientific 3-bullet summary for this gene metadata: {json.dumps(data)}"
                    st.info(f"**🤖 AI Insights:**\n\n{call_gemini(api_key, prompt)}")
            
            with st.expander("View Raw Metadata"):
                st.json(data)
        else:
            st.error("❌ Gene Not Found.")
            st.warning("Check species name. For Bacteria (e.g. lacZ), ensure species is 'escherichia_coli'.")

# --- TAB 2: RELATIONSHIP ANALYSIS ---
with tab2:
    st.subheader("Concise Multi-Gene Relationship Analysis")
    st.write("Find biological connections between a list of genes.")
    
    gene_list_raw = st.text_area("Gene List (separated by commas):", placeholder="TP53, BRCA1, ATM")
    analyze_btn = st.button("Analyze Connection", use_container_width=True)

    if analyze_btn and gene_list_raw:
        if not api_key:
            st.error("API Key Required for AI Analysis.")
        else:
            genes = [g.strip() for g in gene_list_raw.replace("\n", ",").split(",") if g.strip()]
            collected_data = []
            
            pbar = st.progress(0)
            status = st.empty()
            
            for i, g in enumerate(genes):
                status.text(f"Fetching data for {g}...")
                d = hyper_search(species_input, g)
                if d:
                    collected_data.append({"symbol": d.get('display_name'), "desc": d.get('description')})
                pbar.progress((i + 1) / len(genes))
            
            status.empty()
            
            if collected_data:
                st.info(f"Retrieved {len(collected_data)} genes. Synthesizing...")
                with st.spinner("AI finding relationships..."):
                    prompt = f"""
                    Analyze the biological relationships between these genes: {json.dumps(collected_data)}. 
                    Requirements:
                    - Concise, bulleted scientific synthesis. 
                    - Focus strictly on shared pathways, functional networks, or co-expression.
                    - Straight to the point. No introductory or closing text.
                    """
                    analysis = call_gemini(api_key, prompt)
                    st.markdown("### --- CONCISE RELATIONSHIP ANALYSIS ---")
                    st.markdown(analysis)
            else:
                st.error("Could not retrieve data for any of the genes provided. Check species and symbols.")