import streamlit as st
import requests
import json
import re
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

# COMMON ALIASES (Ensures 'escherichia_coli' or 'wheat' maps to the Reference Strain)
SPECIES_MAP = {
    "escherichia_coli": "escherichia_coli_str_k_12_substr_mg1655",
    "wheat": "triticum_aestivum",
    "rice": "oryza_sativa",
    "yeast": "saccharomyces_cerevisiae",
    "arabidopsis": "arabidopsis_thaliana"
}

HEADERS = {"Content-Type": "application/json", "Accept": "application/json"}
MODEL_NAME = "gemini-3.1-flash-preview"

# --- 2. THE SEARCH ENGINE ---

def fetch(base_url, endpoint):
    """Core REST communication."""
    try:
        url = f"{base_url}{endpoint}"
        r = requests.get(url, headers=HEADERS, timeout=15)
        if r.status_code == 200: return r.json()
    except: pass
    return None

def get_bacterial_strains(species_query):
    """Hunts for all available strains if a broad name is given."""
    res = fetch(GENOMES_SERVER, f"/info/species?name={species_query}")
    if res and "species" in res:
        return [s['name'] for s in res['species']]
    return []

def smart_lookup(kingdom, species, query):
    if not query or not species: return None
    
    query = query.strip()
    # Apply alias if exists (e.g. 'wheat' -> 'triticum_aestivum')
    species = SPECIES_MAP.get(species.lower().strip(), species.strip().lower())
    
    # --- ROUTING LOGIC ---
    base_url = KINGDOMS[kingdom]
    
    # FIX: Bread Wheat is physically on the MAIN server, not Genomes.
    is_wheat_id = bool(re.match(r"^Traes", query, re.I))
    if (species == "triticum_aestivum" or is_wheat_id) and kingdom == "Plants":
        base_url = MAIN_SERVER
    # ----------------------

    # STEP 1: Lookup by ID (Recognizes b-numbers for bacteria, Traes for wheat, ENS for others)
    is_id = bool(re.match(r"^(ENS[A-Z]*G|Traes|Os|AT|MLOC|HORVU|Zm|Sobic|b\d{4})\d*", query, re.I))
    if is_id:
        data = fetch(base_url, f"/lookup/id/{query}")
        if data: return data

    # STEP 2: Lookup by Symbol/Xref
    # We try the primary species provided by the user
    data = fetch(base_url, f"/lookup/symbol/{species}/{query}")
    if data: return data
    
    # Try Xref (Deep search for synonyms like 'lacZ' or 'Rht-B1')
    xrefs = fetch(base_url, f"/xrefs/symbol/{species}/{query}")
    if xrefs:
        for item in xrefs:
            if item.get("type") == "gene":
                return fetch(base_url, f"/lookup/id/{item.get('id')}")

    # STEP 3: BACTERIA STRAIN DISCOVERY (The fix for the lacZ error)
    if kingdom == "Bacteria":
        strains = get_bacterial_strains(species)
        # Try the top 5 most likely strains
        for strain in strains[:5]:
            data = fetch(base_url, f"/lookup/symbol/{strain}/{query}")
            if data: return data
            xrefs = fetch(base_url, f"/xrefs/symbol/{strain}/{query}")
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
    # Separated by Kingdom as requested
    selected_kingdom = st.radio("Target Kingdom:", list(KINGDOMS.keys()))
    
    st.markdown("---")
    try:
        api_key = st.secrets["GEMINI_API_KEY"]
        st.success("🤖 AI Engine: Ready")
    except:
        api_key = st.text_input("Gemini API Key:", type="password")

    ai_enabled = st.checkbox("Enable AI Summary", value=True)
    st.markdown("---")
    st.caption("Designed by Maher, MATE-GBI")
    st.caption("Hungarian University of Agriculture and Life Sciences")

# --- MAIN UI ---
st.title("Universal Gene Explorer")
st.info(f"Targeting: **{selected_kingdom}**")

species_input = st.text_input("Species Name (e.g. homo_sapiens, triticum_aestivum, escherichia_coli):", value="homo_sapiens")

tab1, tab2 = st.tabs(["🧬 Single Lookup", "🔗 Relationship Analysis"])

# --- TAB 1: SINGLE LOOKUP ---
with tab1:
    c1, c2 = st.columns([1, 2])
    with c1:
        query_input = st.text_input("Gene Symbol or ID:", placeholder="e.g. lacZ, Rht-B1, BRCA2")
        search_btn = st.button("Fetch Metadata", use_container_width=True)

    if search_btn and query_input:
        with st.spinner(f"Accessing {selected_kingdom} Database..."):
            data = smart_lookup(selected_kingdom, species_input, query_input)
        
        if data:
            st.success(f"✅ Found in {data.get('species')}")
            st.markdown(f"### {data.get('display_name', query_input)}")
            st.write(f"**Description:** {data.get('description', 'N/A')}")
            
            res_c1, res_c2 = st.columns(2)
            with res_c1:
                st.write(f"**ID:** `{data.get('id')}`")
                st.write(f"**Species:** `{data.get('species')}`")
            with res_c2:
                st.write(f"**Location:** {data.get('seq_region_name')}:{data.get('start')}-{data.get('end')}")
                st.write(f"**Assembly:** {data.get('assembly_name')}")

            if ai_enabled and api_key:
                with st.spinner("AI Synthesis..."):
                    prompt = f"Provide a concise, scientific 3-bullet point summary for this gene metadata: {json.dumps(data)}"
                    st.info(f"**🤖 AI Insights:**\n\n{call_ai(api_key, prompt)}")
            
            with st.expander("View Raw Data"):
                st.json(data)
        else:
            st.error(f"❌ '{query_input}' not found in '{species_input}' using the {selected_kingdom} API.")
            st.warning("For Bacteria, the app will auto-scan common strains, but ensure the species name is lowercase_underscore.")

# --- TAB 2: RELATIONSHIP ANALYSIS ---
with tab2:
    st.subheader("Concise Multi-Gene Relationship Analysis")
    gene_list_raw = st.text_area("Gene List (commas):", placeholder="TP53, BRCA1, ATM")
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
                with st.spinner("AI Analysis..."):
                    prompt = f"Analyze biological relationships concisely: {json.dumps(collected_data)}. No intros."
                    st.markdown("### --- CONCISE RELATIONSHIP ANALYSIS ---")
                    st.markdown(call_ai(api_key, prompt))
            else:
                st.error("No data found for the provided list.")