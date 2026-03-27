import streamlit as st
import requests
import json
import re
from google import genai

# --- 1. CONFIGURATION ---
st.set_page_config(page_title="MATE-GBI Gene Explorer", layout="wide")

# DEFINITIVE API SERVERS
# Main Server handles Vertebrates + Bread Wheat (Triticum aestivum)
MAIN_SERVER = "https://rest.ensembl.org"
# Genomes Server handles Other Plants, Bacteria, Fungi, Protists
GENOMES_SERVER = "https://rest.ensemblgenomes.org"

# Strict Category Mapping
KINGDOMS = {
    "Vertebrates": MAIN_SERVER,
    "Plants": GENOMES_SERVER,
    "Bacteria": GENOMES_SERVER,
    "Fungi": GENOMES_SERVER
}

HEADERS = {"Content-Type": "application/json", "Accept": "application/json"}
MODEL_NAME = "gemini-2.5-flash"

# --- 2. THE SEARCH ENGINE ---

def fetch(url):
    try:
        r = requests.get(url, headers=HEADERS, timeout=15)
        if r.status_code == 200: return r.json()
    except: pass
    return None

def resolve_bacterial_strain(species_query):
    """Specific fix for Bacteria: finds the official strain slug."""
    res = fetch(f"{GENOMES_SERVER}/info/species?name={species_query}")
    if res and "species" in res and len(res["species"]) > 0:
        return res["species"][0]["name"]
    return species_query

def smart_lookup(kingdom, species, query):
    if not query or not species: return None
    
    query = query.strip()
    species = species.strip().lower()
    
    # --- ROUTING LOGIC ---
    # Rule 1: Default to the kingdom's server
    base_url = KINGDOMS[kingdom]
    
    # Rule 2: THE WHEAT EXCEPTION (Wheat lives on the Main server now)
    is_wheat_id = bool(re.match(r"^Traes", query, re.I))
    if kingdom == "Plants" and (species == "triticum_aestivum" or is_wheat_id):
        base_url = MAIN_SERVER
        
    # Rule 3: BACTERIA STRAIN RESOLUTION
    search_species = species
    if kingdom == "Bacteria":
        search_species = resolve_bacterial_strain(species)
    # ----------------------

    # STEP A: Try Lookup by ID (ENS..., Traes..., Os..., b0344...)
    is_id = bool(re.match(r"^(ENS[A-Z]*G|Traes|Os|AT|MLOC|HORVU|Zm|Sobic|b\d{4})\d+", query, re.I))
    if is_id:
        data = fetch(f"{base_url}/lookup/id/{query}")
        # Verify species to prevent the "Shark" error
        if data and data.get('species', '').lower() == search_species:
            return data

    # STEP B: Try Lookup by Symbol
    data = fetch(f"{base_url}/lookup/symbol/{search_species}/{query}")
    if data: return data

    # STEP C: Deep Xref Search (For synonyms/common names like Rht-B1 or lacZ)
    xrefs = fetch(f"{base_url}/xrefs/symbol/{search_species}/{query}")
    if xrefs:
        for item in xrefs:
            if item.get("type") == "gene":
                potential_id = item.get("id")
                data = fetch(f"{base_url}/lookup/id/{potential_id}")
                if data and data.get('species', '').lower() == search_species:
                    return data
                    
    return None

def call_ai(api_key, prompt):
    try:
        client = genai.Client(api_key=api_key)
        response = client.models.generate_content(model=MODEL_NAME, contents=prompt)
        return response.text.strip()
    except Exception as e:
        return f"⚠️ AI Error: {str(e)}"

# --- 3. THE INTERFACE ---

with st.sidebar:
    try:
        st.image("mate_logo.png", use_container_width=True)
    except:
        st.title("MATE-GBI")
    
    st.markdown("### 🧬 Database Selection")
    # THE KEY REQUIREMENT: Separated by Kingdom
    selected_kingdom = st.radio("Choose Kingdom:", list(KINGDOMS.keys()))
    
    st.markdown("---")
    try:
        api_key = st.secrets["GEMINI_API_KEY"]
        st.success("🤖 AI Engine: Active")
    except:
        api_key = st.text_input("Gemini API Key:", type="password")

    ai_enabled = st.checkbox("Enable AI Synthesis", value=True)
    st.markdown("---")
    st.caption("Designed by Maher, MATE-GBI")
    st.caption("Hungarian University of Agriculture and Life Sciences")

# --- MAIN UI ---
st.title("Universal Gene Explorer")
st.info(f"Targeting: **{selected_kingdom}** Database")

species_input = st.text_input("Target Species (e.g. homo_sapiens, triticum_aestivum, escherichia_coli):", value="homo_sapiens")

tab1, tab2 = st.tabs(["🧬 Single Lookup", "🔗 Relationship Analysis"])

# --- TAB 1: SINGLE LOOKUP ---
with tab1:
    c1, c2 = st.columns([1, 2])
    with c1:
        query_input = st.text_input("Gene ID or Symbol:", placeholder="e.g. Rht-B1, lacZ, BRCA2")
        search_btn = st.button("Deep Fetch", use_container_width=True)

    if search_btn and query_input:
        with st.spinner(f"Accessing {selected_kingdom} API..."):
            data = smart_lookup(selected_kingdom, species_input, query_input)
        
        if data:
            st.success(f"✅ Found in {data.get('species')}")
            st.markdown(f"### {data.get('display_name', query_input)}")
            st.write(f"**Description:** {data.get('description', 'N/A')}")
            
            res_c1, res_c2 = st.columns(2)
            with res_c1:
                st.write(f"**Species:** `{data.get('species')}`")
                st.write(f"**ID:** `{data.get('id')}`")
                st.write(f"**Biotype:** {data.get('biotype')}")
            with res_c2:
                st.write(f"**Location:** {data.get('seq_region_name')}:{data.get('start')}-{data.get('end')}")
                st.write(f"**Assembly:** {data.get('assembly_name')}")

            if ai_enabled and api_key:
                with st.spinner("AI Synthesis..."):
                    prompt = f"Provide a concise, scientific 3-bullet point summary for this gene: {json.dumps(data)}"
                    st.info(f"**🤖 AI Insights:**\n\n{call_ai(api_key, prompt)}")
            
            with st.expander("View Raw Metadata"):
                st.json(data)
        else:
            st.error(f"❌ '{query_input}' not found in '{species_input}' on the {selected_kingdom} server.")

# --- TAB 2: RELATIONSHIP ANALYSIS ---
with tab2:
    st.subheader("Concise Multi-Gene Relationship Analysis")
    gene_list_raw = st.text_area("Gene list (commas):", placeholder="BRCA1, BRCA2, ATM")
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
                with st.spinner("AI Synthesis..."):
                    prompt = f"Analyze relationships concisely: {json.dumps(collected_data)}. Focus on pathways. No intros."
                    st.markdown("### --- CONCISE RELATIONSHIP ANALYSIS ---")
                    st.markdown(call_ai(api_key, prompt))
            else:
                st.error("Could not find any of these genes in the selected kingdom.")