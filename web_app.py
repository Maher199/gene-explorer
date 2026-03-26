import streamlit as st
import requests
import json
import re
from google import genai

# --- 1. Configuration ---
st.set_page_config(page_title="MATE-GBI Gene Explorer", layout="wide")

SERVERS = {
    "Vertebrates (Ensembl Main)": "https://rest.ensembl.org",
    "Plants (Ensembl Genomes)": "https://rest.ensemblgenomes.org",
    "Bacteria (Ensembl Genomes)": "https://rest.ensemblgenomes.org",
    "Fungi (Ensembl Genomes)": "https://rest.ensemblgenomes.org"
}

HEADERS = {"Content-Type": "application/json", "Accept": "application/json"}
MODEL_NAME = "gemini-3-flash-preview"

# --- 2. Logic Functions ---

def fetch_api(base_url, endpoint):
    try:
        url = f"{base_url}{endpoint}"
        response = requests.get(url, headers=HEADERS, timeout=15)
        if response.status_code == 200:
            return response.json()
    except:
        pass
    return None

def smart_lookup(base_url, species, query):
    """
    Strict search logic:
    1. Check if it's a known Ensembl ID format (ENSG, Traes, etc.)
    2. Search by Symbol using the EXACT species provided.
    3. Search Xrefs ONLY within that specific species.
    """
    if not query or not species: return None
    
    # Improved ID Regex: Must start with specific prefixes followed by numbers
    # This prevents names like 'BRCA2' or 'lacZ' from being treated as IDs
    is_id = bool(re.match(r"^(ENS[A-Z]*G|Traes|Os|AT|MLOC|HORVU|Zm|Sobic|b\d{4})\d+", query, re.I))
    
    # A. If it's an ID, look it up globally
    if is_id:
        data = fetch_api(base_url, f"/lookup/id/{query}")
        if data: return data

    # B. Standard Symbol Lookup (Strictly using the user's species)
    data = fetch_api(base_url, f"/lookup/symbol/{species}/{query}")
    if data: return data

    # C. Xref Search (As a backup for synonyms, but still locked to the user's species)
    xrefs = fetch_api(base_url, f"/xrefs/symbol/{species}/{query}")
    if xrefs:
        for item in xrefs:
            if item.get("type") == "gene":
                # Ensure the found ID belongs to the correct species before returning
                lookup_data = fetch_api(base_url, f"/lookup/id/{item.get('id')}")
                if lookup_data and lookup_data.get('species').lower() == species.lower():
                    return lookup_data
    return None

def call_gemini(api_key, prompt):
    try:
        client = genai.Client(api_key=api_key)
        response = client.models.generate_content(model=MODEL_NAME, contents=prompt)
        return response.text.strip()
    except Exception as e:
        return f"⚠️ AI Error: {str(e)}"

# --- 3. Sidebar ---

with st.sidebar:
    try:
        st.image("mate_logo.png", use_container_width=True)
    except:
        st.title("MATE-GBI")
    
    st.markdown("### 🖥️ Database Settings")
    selected_division = st.selectbox("Choose Division:", list(SERVERS.keys()))
    base_url = SERVERS[selected_division]

    st.markdown("---")
    try:
        api_key = st.secrets["GEMINI_API_KEY"]
        st.success("🤖 AI Active")
    except:
        api_key = st.text_input("Gemini API Key:", type="password")

    ai_enabled = st.checkbox("Enable AI Analysis", value=True)
    st.caption("Designed by Maher, MATE-GBI")
    st.caption("Hungarian University of Agriculture and Life Science")

# --- 4. Main Interface ---

st.title("🧬 Gene Explorer")

# User types the species name here
species_input = st.text_input("Species Name (e.g. homo_sapiens, triticum_aestivum, escherichia_coli):", value="homo_sapiens")

tab1, tab2 = st.tabs(["🔍 Single Lookup", "🔗 Relationship Analysis"])

with tab1:
    c1, c2 = st.columns([1, 2])
    with c1:
        query_input = st.text_input("Gene Symbol or ID:", placeholder="e.g. BRCA2, Rht-B1")
        search_btn = st.button("Fetch Data", use_container_width=True)

    if search_btn and query_input:
        with st.spinner(f"Querying {selected_division}..."):
            data = smart_lookup(base_url, species_input, query_input)
        
        if data:
            # Final safety check: Does the result species match the input species?
            res_species = data.get('species', '').lower()
            if res_species != species_input.lower() and selected_division != "Bacteria":
                st.warning(f"Note: Found a close match in '{res_species}'.")

            st.success(f"✅ Results for: {data.get('display_name', query_input)}")
            st.markdown(f"**Description:** {data.get('description', 'N/A')}")
            
            res_c1, res_c2 = st.columns(2)
            with res_c1:
                st.write(f"**Species:** `{data.get('species')}`")
                st.write(f"**Ensembl ID:** `{data.get('id')}`")
            with res_c2:
                st.write(f"**Location:** {data.get('seq_region_name')}:{data.get('start')}-{data.get('end')}")
                st.write(f"**Biotype:** {data.get('biotype')}")

            if ai_enabled and api_key:
                with st.spinner("AI Thinking..."):
                    prompt = f"Provide a concise, scientific 3-bullet summary for this gene metadata: {json.dumps(data)}"
                    st.info(f"**🤖 AI Analysis:**\n\n{call_gemini(api_key, prompt)}")
        else:
            st.error(f"❌ '{query_input}' not found in '{species_input}' on the {selected_division} server.")

with tab2:
    st.subheader("Multi-Gene Relationship Analysis")
    gene_list_raw = st.text_area("Gene symbols (separated by commas):", placeholder="TP53, BRCA1, ATM")
    analyze_btn = st.button("Analyze Connections")

    if analyze_btn and gene_list_raw:
        if not api_key:
            st.error("API Key Required.")
        else:
            genes = [g.strip() for g in gene_list_raw.replace("\n", ",").split(",") if g.strip()]
            collected_data = []
            pbar = st.progress(0)
            for i, g in enumerate(genes):
                d = smart_lookup(base_url, species_input, g)
                if d:
                    collected_data.append({"symbol": d.get('display_name'), "desc": d.get('description')})
                pbar.progress((i + 1) / len(genes))
            
            if collected_data:
                prompt = f"Analyze relationships concisely: {json.dumps(collected_data)}. No intros."
                st.markdown("### --- ANALYSIS ---")
                st.write(call_gemini(api_key, prompt))