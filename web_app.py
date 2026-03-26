import streamlit as st
import requests
import json
import re
from google import genai

# --- 1. Configuration ---
st.set_page_config(page_title="MATE-GBI Universal Gene Explorer", layout="wide")

SERVERS = [
    "https://rest.ensembl.org",        
    "https://rest.ensemblgenomes.org"  
]

HEADERS = {"Content-Type": "application/json", "Accept": "application/json"}
MODEL_NAME = "gemini-2.5-flash"

# --- 2. Logic Functions ---

def fetch_api(base_url, endpoint):
    try:
        response = requests.get(f"{base_url}{endpoint}", headers=HEADERS, timeout=15)
        if response.status_code == 200:
            return response.json()
    except:
        pass
    return None

def resolve_species(query_species):
    """
    If a user types 'escherichia_coli', this looks for the 
    best 'official slug' (e.g., escherichia_coli_str_k_12_substr_mg1655).
    """
    for base_url in SERVERS:
        # Search for species matches
        res = fetch_api(base_url, f"/info/species?name={query_species}")
        if res and "species" in res and len(res["species"]) > 0:
            return res["species"][0]["name"]
    return query_species

def hyper_search(species, query):
    if not query: return None
    
    # Clean up the species name first
    official_species = resolve_species(species)
    
    is_id = bool(re.match(r"^(ENS|Traes|Os|AT|MLOC|Zm|Sobic|b\d{4}).*", query, re.I))

    for base_url in SERVERS:
        # 1. Try ID
        if is_id:
            data = fetch_api(base_url, f"/lookup/id/{query}")
            if data: return data

        # 2. Try Symbol with Official Species Name
        data = fetch_api(base_url, f"/lookup/symbol/{official_species}/{query}")
        if data: return data

        # 3. Deep Xref Search
        xrefs = fetch_api(base_url, f"/xrefs/symbol/{official_species}/{query}")
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
st.info("Searching Bacteria, Plants, and Vertebrates. Strains are automatically resolved.")

species_input = st.text_input("Species Name (e.g. escherichia_coli or triticum_aestivum):", value="escherichia_coli")
query_input = st.text_input("Gene ID or Symbol (e.g. lacZ or b0344):", placeholder="e.g. lacZ")

if st.button("Deep Search", use_container_width=True) and query_input:
    with st.spinner(f"Identifying correct strain and fetching '{query_input}'..."):
        data = hyper_search(species_input, query_input)
    
    if data:
        st.success(f"✅ Found in strain: {data.get('species')}")
        st.markdown(f"### Gene: {data.get('display_name', query_input)}")
        st.markdown(f"**Description:** {data.get('description', 'N/A')}")
        
        c1, c2 = st.columns(2)
        with c1:
            st.write(f"**Stable ID:** `{data.get('id')}`")
            st.write(f"**Biotype:** {data.get('biotype')}")
        with c2:
            st.write(f"**Location:** {data.get('seq_region_name')}:{data.get('start')}-{data.get('end')}")

        if ai_enabled and api_key:
            with st.spinner("Generating AI Analysis..."):
                prompt = f"Provide a concise, scientific 3-bullet summary of this gene metadata: {json.dumps(data)}"
                st.info(f"**🤖 AI Insights:**\n\n{call_gemini(api_key, prompt)}")
        
        with st.expander("View Full Metadata"):
            st.json(data)
    else:
        st.error("❌ Gene Not Found.")
        st.warning("For Bacteria, try the Locus Tag (e.g., b0344) if 'lacZ' fails.")