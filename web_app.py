import streamlit as st
import requests
import json
import re
from google import genai

# --- 1. Configuration ---
st.set_page_config(page_title="MATE-GBI Universal Gene Explorer", layout="wide")

# Unified Server List: We will try them in order automatically
SERVERS = [
    "https://rest.ensembl.org",        # Main Server (Now includes most Plants/Wheat)
    "https://rest.ensemblgenomes.org"  # Secondary Server (Microbes/Older Plant data)
]

HEADERS = {"Content-Type": "application/json", "Accept": "application/json"}
MODEL_NAME = "gemini-2.5-flash"

# --- 2. Advanced Search Logic ---

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

def hyper_search(species, query):
    """
    Tries every possible combination across all servers:
    1. ID lookup on Main then Secondary
    2. Symbol lookup on Main then Secondary
    3. Xref lookup (Deep search) on Main then Secondary
    """
    if not query: return None
    
    # Check if input is likely an ID (ENSG... or TraesCS...)
    is_id = bool(re.match(r"^(ENS|Traes|Os|AT|MLOC|Zm|Sobic).*", query, re.I))

    for base_url in SERVERS:
        # Step A: Lookup by ID
        if is_id:
            data = fetch_api(base_url, f"/lookup/id/{query}")
            if data: return data

        # Step B: Lookup by Symbol
        data = fetch_api(base_url, f"/lookup/symbol/{species}/{query}")
        if data: return data

        # Step C: Deep Xref Search (For synonyms like Rht-B1)
        xrefs = fetch_api(base_url, f"/xrefs/symbol/{species}/{query}")
        if xrefs:
            for item in xrefs:
                if item.get("type") == "gene":
                    data = fetch_api(base_url, f"/lookup/id/{item.get('id')}")
                    if data: return data
    return None

def call_gemini(api_key, prompt):
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
    
    st.markdown("### 🔑 Configuration")
    try:
        api_key = st.secrets["GEMINI_API_KEY"]
        st.success("🤖 AI Engine Active")
    except:
        api_key = st.text_input("Gemini API Key:", type="password")

    ai_enabled = st.checkbox("Enable AI Synthesis", value=True)
    st.markdown("---")
    st.caption("Designed by Maher, MATE-GBI")

# --- 4. Main UI ---
st.title("🧬 Universal Gene Explorer")
st.info("Searching across all Ensembl divisions (Vertebrates, Plants, Bacteria, Fungi).")

tab1, tab2 = st.tabs(["Single Gene Search", "Relationship Analysis"])

with tab1:
    c1, c2 = st.columns([1, 2])
    with c1:
        species_input = st.text_input("Species Name:", value="triticum_aestivum")
        query_input = st.text_input("Gene ID or Symbol:", placeholder="e.g. TraesCS4B02G043100 or Rht-B1")
        search_btn = st.button("Deep Search", use_container_width=True)

    if search_btn and query_input:
        with st.spinner(f"Scanning all Ensembl servers for '{query_input}'..."):
            data = hyper_search(species_input, query_input)
        
        if data:
            st.success(f"✅ Result Found!")
            
            # Metadata Display
            st.markdown(f"### {data.get('display_name', query_input)}")
            st.markdown(f"**Description:** {data.get('description', 'N/A')}")
            
            res_col1, res_col2 = st.columns(2)
            with res_col1:
                st.write(f"**Stable ID:** `{data.get('id')}`")
                st.write(f"**Biotype:** {data.get('biotype')}")
                st.write(f"**Species:** {data.get('species')}")
            with res_col2:
                st.write(f"**Chromosome:** {data.get('seq_region_name')}")
                st.write(f"**Position:** {data.get('start')}-{data.get('end')}")
                st.write(f"**Assembly:** {data.get('assembly_name')}")

            if ai_enabled and api_key:
                with st.spinner("Generating AI Analysis..."):
                    prompt = f"Provide a concise, scientific 3-bullet summary of this gene metadata: {json.dumps(data)}"
                    summary = call_gemini(api_key, prompt)
                    st.info(f"**🤖 AI Insights:**\n\n{summary}")
            
            with st.expander("View Full API Response"):
                st.json(data)
        else:
            st.error("❌ Gene Not Found.")
            st.warning("Make sure the species name is in 'lowercase_underscore' format (e.g. triticum_aestivum).")

# (Relationship Analysis tab logic follows the same hyper_search pattern)
with tab2:
    st.subheader("Concise Multi-Gene Relationship Analysis")
    gene_list_raw = st.text_area("Gene List:", placeholder="TP53, BRCA1, ATM")
    analyze_btn = st.button("Analyze Connection")

    if analyze_btn and gene_list_raw:
        if not api_key:
            st.error("API Key Required")
        else:
            genes = [g.strip() for g in gene_list_raw.replace("\n", ",").split(",") if g.strip()]
            collected = []
            pbar = st.progress(0)
            for i, g in enumerate(genes):
                d = hyper_search(species_input, g)
                if d: collected.append({"symbol": d.get('display_name'), "desc": d.get('description')})
                pbar.progress((i + 1) / len(genes))
            
            if collected:
                with st.spinner("AI Synthesis..."):
                    prompt = f"Analyze biological relationships concisely: {json.dumps(collected)}. Focus on shared pathways. No intros."
                    st.markdown("### --- ANALYSIS ---")
                    st.write(call_gemini(api_key, prompt))
            else:
                st.error("Could not find any genes from the list.")