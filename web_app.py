import streamlit as st
import requests
import json
import re
from google import genai

# --- 1. CONFIGURATION ---
st.set_page_config(page_title="MATE-GBI Universal Gene Explorer", layout="wide")

MAIN_SERVER = "https://rest.ensembl.org"
GENOMES_SERVER = "https://rest.ensemblgenomes.org"

# DATABASE CATEGORIES
KINGDOMS = {
    "Vertebrates": MAIN_SERVER,
    "Plants": GENOMES_SERVER,
    "Bacteria": GENOMES_SERVER,
    "Fungi": GENOMES_SERVER
}

HEADERS = {"Content-Type": "application/json", "Accept": "application/json"}
MODEL_NAME = "gemini-3-flash-preview"

# --- 2. THE SEARCH ENGINE ---

def fetch(base_url, endpoint):
    try:
        url = f"{base_url}{endpoint}"
        r = requests.get(url, headers=HEADERS, timeout=12)
        if r.status_code == 200: return r.json()
    except: pass
    return None

def resolve_exact_species(base_url, species_query):
    """
    Finds the exact 'slug' name on the server.
    Fixes 'hordeum_vulgare' -> 'hordeum_vulgare_core_51_91_3' or similar.
    """
    res = fetch(base_url, f"/info/species?name={species_query}")
    if res and "species" in res and len(res["species"]) > 0:
        return res["species"][0]["name"]
    return species_query

def smart_lookup(kingdom, species, query):
    if not query or not species: return None
    
    query = query.strip()
    # Route Wheat to Main server, others to Genomes/Main based on selection
    is_wheat = bool(re.match(r"^Traes", query, re.I)) or "triticum_aestivum" in species.lower()
    base_url = MAIN_SERVER if (is_wheat or kingdom == "Vertebrates") else KINGDOMS[kingdom]
    
    # Get the official species slug (Essential for Barley/Bacteria)
    official_sp = resolve_exact_species(base_url, species.strip().lower())

    # STEP 1: ID Search (Direct)
    is_id = bool(re.match(r"^(ENS|Traes|Os|AT|MLOC|HORVU|Zm|Sobic|b\d{4})", query, re.I))
    if is_id:
        data = fetch(base_url, f"/lookup/id/{query}")
        if data: return data

    # STEP 2: Xref Search (Deep search for synonyms like Hox1, Vrs1, lacZ)
    # This is the most robust way to find genes by common names in Plants/Bacteria
    xrefs = fetch(base_url, f"/xrefs/symbol/{official_sp}/{query}")
    if not xrefs:
        # Try a case-insensitive variant if first fails
        xrefs = fetch(base_url, f"/xrefs/symbol/{official_sp}/{query.capitalize()}")
    
    if xrefs:
        for item in xrefs:
            if item.get("type") == "gene":
                gene_id = item.get("id")
                return fetch(base_url, f"/lookup/id/{gene_id}")

    # STEP 3: Last Resort - Standard Lookup
    return fetch(base_url, f"/lookup/symbol/{official_sp}/{query}")

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
    
    st.markdown("### 🖥️ Database Connection")
    selected_kingdom = st.radio("Target Kingdom:", list(KINGDOMS.keys()), index=1) # Default to Plants
    
    st.markdown("---")
    try:
        api_key = st.secrets["GEMINI_API_KEY"]
        st.success("🤖 AI Engine: Active")
    except:
        api_key = st.text_input("Gemini API Key:", type="password")

    ai_enabled = st.checkbox("Enable AI Analysis", value=True)
    st.caption("Designed by Maher, MATE-GBI")

# --- MAIN UI ---
st.title("🧬 Universal Gene Explorer")
st.info(f"Connected to **{selected_kingdom}** Division")

species_input = st.text_input("Species Name (e.g. hordeum_vulgare, triticum_aestivum, escherichia_coli):", value="hordeum_vulgare")

tab1, tab2 = st.tabs(["🔍 Single Lookup", "🔗 Relationship Analysis"])

# --- TAB 1: SINGLE LOOKUP ---
with tab1:
    c1, c2 = st.columns([1, 2])
    with c1:
        query_input = st.text_input("Gene Symbol or ID:", placeholder="e.g. Hox1, Vrs1, b0344")
        search_btn = st.button("Deep Search", use_container_width=True)

    if search_btn and query_input:
        with st.spinner(f"Running deep search for '{query_input}'..."):
            data = smart_lookup(selected_kingdom, species_input, query_input)
        
        if data:
            st.success(f"✅ Gene Found!")
            st.markdown(f"### {data.get('display_name', query_input)}")
            st.write(f"**Description:** {data.get('description', 'No description found in Ensembl.')}")
            
            res_c1, res_c2 = st.columns(2)
            with res_c1:
                st.write(f"**Species:** `{data.get('species')}`")
                st.write(f"**Ensembl ID:** `{data.get('id')}`")
            with res_c2:
                st.write(f"**Location:** {data.get('seq_region_name')}:{data.get('start')}-{data.get('end')}")
                st.write(f"**Assembly:** {data.get('assembly_name')}")

            if ai_enabled and api_key:
                with st.spinner("AI Synthesis..."):
                    prompt = f"Summarize this gene concisely (3 bullets): {json.dumps(data)}"
                    st.info(f"**🤖 AI Analysis:**\n\n{call_ai(api_key, prompt)}")
            
            with st.expander("View Raw Data"):
                st.json(data)
        else:
            st.error(f"❌ '{query_input}' not found in '{species_input}'.")
            st.warning("Tip: For Barley, ensure the Kingdom is 'Plants'. If 'Hox1' fails, try 'Vrs1'.")

# --- TAB 2: RELATIONSHIP ANALYSIS ---
with tab2:
    st.subheader("Concise Multi-Gene Relationship Analysis")
    gene_list_raw = st.text_area("Gene List (commas):", placeholder="Vrs1, Hox2, HvHox1")
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
                    prompt = f"Analyze biological relationships concisely: {json.dumps(collected_data)}. No intros."
                    st.markdown("### --- CONCISE RELATIONSHIP ANALYSIS ---")
                    st.write(call_ai(api_key, prompt))
            else:
                st.error("No genes found for the provided list.")