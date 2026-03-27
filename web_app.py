import streamlit as st
import requests
import json
import re
import time
from google import genai

# --- 1. CONFIGURATION ---
st.set_page_config(page_title="MATE-GBI Universal Gene Explorer", layout="wide")

# PHYSICAL SERVERS
MAIN_SERVER = "https://rest.ensembl.org"
GENOMES_SERVER = "https://rest.ensemblgenomes.org"

# KINGDOM TO SERVER MAPPING
KINGDOMS = {
    "Vertebrates": MAIN_SERVER,
    "Plants": GENOMES_SERVER,
    "Bacteria": GENOMES_SERVER,
    "Fungi": GENOMES_SERVER
}

# REFERENCE STRAIN ALIASES (The most common strains for research)
SPECIES_MAP = {
    "escherichia_coli": "escherichia_coli_gca_000005845", # K-12 MG1655 (The most common reference)
    "wheat": "triticum_aestivum",
    "rice": "oryza_sativa",
    "yeast": "saccharomyces_cerevisiae",
    "arabidopsis": "arabidopsis_thaliana"
}

HEADERS = {"Content-Type": "application/json", "Accept": "application/json"}
MODEL_NAME = "gemini-3-flash"

# --- 2. THE SEARCH ENGINE ---

def fetch(base_url, endpoint):
    try:
        url = f"{base_url}{endpoint}"
        r = requests.get(url, headers=HEADERS, timeout=15)
        if r.status_code == 200: return r.json()
    except: pass
    return None

def smart_lookup(kingdom, species, query):
    if not query or not species: return None
    
    query = query.strip()
    input_species = species.strip().lower()
    
    # Apply reference alias (e.g. 'escherichia_coli' -> 'escherichia_coli_gca_000005845')
    species_slug = SPECIES_MAP.get(input_species, input_species)
    
    # --- SERVER ROUTING ---
    base_url = KINGDOMS[kingdom]
    # WHEAT EXCEPTION: Wheat lives on the MAIN server
    is_wheat_id = bool(re.match(r"^Traes", query, re.I))
    if (species_slug == "triticum_aestivum" or is_wheat_id) and kingdom == "Plants":
        base_url = MAIN_SERVER

    # STEP 1: Direct ID Lookup (Supports ENS..., Traes..., Os..., b0344...)
    # We broaden the ID regex to capture Bacterial Locus Tags (like b0344)
    is_id = bool(re.match(r"^(ENS[A-Z]*G|Traes|Os|AT|MLOC|HORVU|Zm|Sobic|b\d{4})", query, re.I))
    if is_id:
        data = fetch(base_url, f"/lookup/id/{query}")
        if data: return data

    # STEP 2: Standard Symbol Lookup (Using the resolved strain)
    data = fetch(base_url, f"/lookup/symbol/{species_slug}/{query}")
    if data: return data
    
    # STEP 3: Xref Search (Deep search for synonyms like 'lacZ' or 'Rht-B1')
    xrefs = fetch(base_url, f"/xrefs/symbol/{species_slug}/{query}")
    if xrefs:
        for item in xrefs:
            if item.get("type") == "gene":
                return fetch(base_url, f"/lookup/id/{item.get('id')}")

    # STEP 4: BACTERIA FALLBACK (If the primary strain failed, try the alternate slug)
    if kingdom == "Bacteria" and input_species == "escherichia_coli":
        alt_strain = "escherichia_coli_str_k_12_substr_mg1655"
        data = fetch(base_url, f"/lookup/symbol/{alt_strain}/{query}")
        if data: return data
        xrefs = fetch(base_url, f"/xrefs/symbol/{alt_strain}/{query}")
        if xrefs:
            for item in xrefs:
                if item.get("type") == "gene":
                    return fetch(base_url, f"/lookup/id/{item.get('id')}")
                    
    return None

def call_ai(api_key, prompt):
    try:
        client = genai.Client(api_key=api_key)
        response = client.models.generate_content(model=MODEL_NAME, contents=prompt)
        return response.text.strip()
    except Exception as e:
        return f"⚠️ AI Error: {str(e)}"

# --- 3. THE UI ---

with st.sidebar:
    try:
        st.image("mate_logo.png", use_container_width=True)
    except:
        st.title("MATE-GBI")
    
    st.markdown("### 🧬 Database Selection")
    selected_kingdom = st.radio("Target Kingdom:", list(KINGDOMS.keys()))
    
    st.markdown("---")
    try:
        api_key = st.secrets["GEMINI_API_KEY"]
        st.success("🤖 AI Engine: Active")
    except:
        api_key = st.text_input("Gemini API Key:", type="password")

    ai_enabled = st.checkbox("Enable AI Summary", value=True)
    st.markdown("---")
    st.caption("Designed by Maher, MATE-GBI")

# --- MAIN UI ---
st.title("Universal Gene Explorer")

species_input = st.text_input("Target Species (e.g. homo_sapiens, triticum_aestivum, escherichia_coli):", value="escherichia_coli")

tab1, tab2 = st.tabs(["🧬 Single Lookup", "🔗 Relationship Analysis"])

# --- TAB 1: SINGLE LOOKUP ---
with tab1:
    c1, c2 = st.columns([1, 2])
    with c1:
        query_input = st.text_input("Gene Symbol or ID:", placeholder="e.g. lacZ, Rht-B1, BRCA2")
        search_btn = st.button("Deep Fetch Data", use_container_width=True)

    if search_btn and query_input:
        with st.spinner(f"Accessing {selected_kingdom} Server..."):
            data = smart_lookup(selected_kingdom, species_input, query_input)
        
        if data:
            st.success(f"✅ Found in: {data.get('species')}")
            st.markdown(f"### {data.get('display_name', query_input)}")
            st.write(f"**Description:** {data.get('description', 'N/A')}")
            
            res_c1, res_c2 = st.columns(2)
            with res_c1:
                st.write(f"**Ensembl ID:** `{data.get('id')}`")
                st.write(f"**Biotype:** {data.get('biotype')}")
            with res_c2:
                st.write(f"**Location:** {data.get('seq_region_name')}:{data.get('start')}-{data.get('end')}")
                st.write(f"**Assembly:** {data.get('assembly_name')}")

            if ai_enabled and api_key:
                with st.spinner("AI Synthesizing..."):
                    prompt = f"Summarize this gene concisely (3 bullets): {json.dumps(data)}"
                    st.info(f"**🤖 AI Insights:**\n\n{call_ai(api_key, prompt)}")
        else:
            st.error(f"❌ '{query_input}' not found in '{species_input}'.")
            st.warning("Bacteria Tip: Try searching for the Locus Tag (e.g. 'b0344' for lacZ) if the common symbol fails.")

# --- TAB 2: RELATIONSHIP ANALYSIS ---
with tab2:
    st.subheader("Concise Multi-Gene Relationship Analysis")
    gene_list_raw = st.text_area("Gene symbols (commas):", placeholder="lacZ, lacY, lacA")
    analyze_btn = st.button("Analyze Connections", use_container_width=True)

    if analyze_btn and gene_list_raw:
        if not api_key:
            st.error("API Key Required.")
        else:
            genes = [g.strip() for g in gene_list_raw.replace("\n", ",").split(",") if g.strip()]
            collected_data = []
            pbar = st.progress(0)
            for i, g in enumerate(genes):
                d = smart_lookup(selected_kingdom, species_input, g)
                if d:
                    collected_data.append({"symbol": d.get('display_name'), "desc": d.get('description')})
                pbar.progress((i + 1) / len(genes))
            
            if collected_data:
                with st.spinner("AI Analyzing..."):
                    prompt = f"Analyze biological relationships concisely: {json.dumps(collected_data)}. No intros."
                    st.markdown("### --- CONCISE RELATIONSHIP ANALYSIS ---")
                    st.markdown(call_ai(api_key, prompt))
            else:
                st.error("No genes from your list were found.")