import streamlit as st
import requests
import json
import re
from google import genai

# --- 1. CONFIGURATION ---
st.set_page_config(page_title="MATE-GBI Universal Explorer", layout="wide")

MAIN_SERVER = "https://rest.ensembl.org"
GENOMES_SERVER = "https://rest.ensemblgenomes.org"

# Category Mapping
KINGDOMS = {
    "Vertebrates": MAIN_SERVER,
    "Plants": GENOMES_SERVER,
    "Bacteria": GENOMES_SERVER,
    "Fungi": GENOMES_SERVER
}

HEADERS = {"Content-Type": "application/json", "Accept": "application/json"}
MODEL_NAME = "gemini-3-flash-preview"

# --- 2. THE SELF-HEALING SEARCH ENGINE ---

def fetch(base_url, endpoint):
    """Core communication with Ensembl REST."""
    try:
        url = f"{base_url}{endpoint}"
        r = requests.get(url, headers=HEADERS, timeout=12)
        if r.status_code == 200: return r.json()
    except: pass
    return None

def find_actual_species_slugs(base_url, query_species):
    """Finds all official species names that match the user's input."""
    res = fetch(base_url, f"/info/species?name={query_species}")
    if res and "species" in res:
        return [s['name'] for s in res['species']]
    return [query_species]

def smart_lookup(selected_kingdom, species, query):
    if not query or not species: return None
    
    query = query.strip()
    species_input = species.strip().lower()
    
    # 1. Prepare Server List (Try selected kingdom first, then the other as fallback)
    primary_server = KINGDOMS[selected_kingdom]
    secondary_server = MAIN_SERVER if primary_server == GENOMES_SERVER else GENOMES_SERVER
    servers_to_try = [primary_server, secondary_server]

    # 2. Try ID Lookup First (Global)
    is_id = bool(re.match(r"^(ENS|Traes|Os|AT|MLOC|HORVU|Zm|Sobic|b\d{4})", query, re.I))
    if is_id:
        for srv in servers_to_try:
            data = fetch(srv, f"/lookup/id/{query}")
            if data: return data

    # 3. Try Symbol Search with Species Auto-Discovery
    for srv in servers_to_try:
        # Resolve potential species names (e.g. 'hordeum_vulgare' vs 'hordeum_vulgare_core_...')
        potential_slugs = find_actual_species_slugs(srv, species_input)
        
        for slug in potential_slugs:
            # Try A: Direct Symbol
            data = fetch(srv, f"/lookup/symbol/{slug}/{query}")
            if data: return data
            
            # Try B: Xref (The 'Vrs1' fix)
            xrefs = fetch(srv, f"/xrefs/symbol/{slug}/{query}")
            if xrefs:
                for item in xrefs:
                    if item.get("type") == "gene":
                        res = fetch(srv, f"/lookup/id/{item.get('id')}")
                        if res: return res
                        
    return None

def call_ai(api_key, prompt):
    try:
        client = genai.Client(api_key=api_key)
        response = client.models.generate_content(model=MODEL_NAME, contents=prompt)
        return response.text.strip()
    except Exception as e:
        return f"⚠️ AI Error: {str(e)}"

# --- 3. UI SIDEBAR ---

with st.sidebar:
    try:
        st.image("mate_logo.png", use_container_width=True)
    except:
        st.title("MATE-GBI")
    
    st.markdown("### 🧬 Database Selection")
    kingdom = st.radio("Primary Kingdom:", list(KINGDOMS.keys()), index=1)
    
    st.markdown("---")
    try:
        api_key = st.secrets["GEMINI_API_KEY"]
        st.success("🤖 AI Engine: Active")
    except:
        api_key = st.text_input("Gemini API Key:", type="password")

    ai_enabled = st.checkbox("Enable AI Analysis", value=True)
    st.caption("Designed by Maher, MATE-GBI")

# --- 4. MAIN UI ---

st.title("Universal Gene Explorer")
st.info(f"Targeting: **{kingdom}** (with multi-server fallback)")

species_input = st.text_input("Species Name:", value="hordeum_vulgare")

tab1, tab2 = st.tabs(["🔍 Single Lookup", "🔗 Relationship Analysis"])

# --- TAB 1: SINGLE LOOKUP ---
with tab1:
    c1, c2 = st.columns([1, 2])
    with c1:
        query_input = st.text_input("Gene Name or ID:", placeholder="e.g. Vrs1, b0344, BRCA2")
        search_btn = st.button("Deep Search", use_container_width=True)

    if search_btn and query_input:
        with st.spinner(f"Scanning Ensembl for '{query_input}'..."):
            data = smart_lookup(kingdom, species_input, query_input)
        
        if data:
            st.success(f"✅ Found in: {data.get('species')}")
            st.markdown(f"### {data.get('display_name', query_input)}")
            st.write(f"**Description:** {data.get('description', 'No description found.')}")
            
            res_c1, res_c2 = st.columns(2)
            with res_c1:
                st.write(f"**Stable ID:** `{data.get('id')}`")
                st.write(f"**Species:** `{data.get('species')}`")
            with res_c2:
                st.write(f"**Location:** {data.get('seq_region_name')}:{data.get('start')}-{data.get('end')}")
                st.write(f"**Assembly:** {data.get('assembly_name')}")

            if ai_enabled and api_key:
                with st.spinner("AI Analysis..."):
                    prompt = f"Provide a concise scientific 3-bullet summary of: {json.dumps(data)}"
                    st.info(f"**🤖 AI Insights:**\n\n{call_ai(api_key, prompt)}")
        else:
            st.error(f"❌ '{query_input}' not found.")
            st.warning("Note: Plant genes are notoriously difficult. Ensure you are using the correct server and official Locus IDs if names fail.")

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
                d = smart_lookup(kingdom, species_input, g)
                if d:
                    collected_data.append({"symbol": d.get('display_name'), "desc": d.get('description')})
                pbar.progress((i + 1) / len(genes))
            
            if collected_data:
                with st.spinner("AI Synthesizing..."):
                    prompt = f"Analyze biological relationships concisely: {json.dumps(collected_data)}. No intros."
                    st.markdown("### --- CONCISE RELATIONSHIP ANALYSIS ---")
                    st.write(call_ai(api_key, prompt))
            else:
                st.error("No genes found for the provided list.")