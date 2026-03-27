import streamlit as st
import requests
import json
import re
from google import genai

# --- 1. Configuration ---
st.set_page_config(page_title="MATE-GBI Gene Explorer", layout="wide")

# The two physical Ensembl API locations
MAIN_SERVER = "https://rest.ensembl.org"
GENOMES_SERVER = "https://rest.ensemblgenomes.org"

# Display names for the UI
SERVER_OPTIONS = {
    "Vertebrates / Wheat": MAIN_SERVER,
    "Other Plants / Bacteria / Fungi": GENOMES_SERVER
}

HEADERS = {"Content-Type": "application/json", "Accept": "application/json"}
MODEL_NAME = "gemini-3.1-flash-preview"

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

def smart_lookup(user_selected_url, species, query):
    """
    Advanced Routing: 
    If ID starts with 'Traes', force the Main Server (Wheat Exception).
    Otherwise, use the user's selected server.
    """
    if not query or not species: return None
    
    # 1. Detect Wheat ID (Traes...)
    is_wheat_id = bool(re.match(r"^Traes", query, re.I))
    is_ensembl_id = bool(re.match(r"^(ENS[A-Z]*G|Os|AT|MLOC|HORVU|Zm|Sobic|b\d{4})\d+", query, re.I))
    
    # Decide which server to try first
    primary_url = MAIN_SERVER if (is_wheat_id or "human" in species or "homo" in species) else user_selected_url
    
    # A. ID Lookup
    if is_wheat_id or is_ensembl_id:
        data = fetch_api(primary_url, f"/lookup/id/{query}")
        if data: return data
        # Fallback to the other server if ID not found on primary
        secondary_url = GENOMES_SERVER if primary_url == MAIN_SERVER else MAIN_SERVER
        data = fetch_api(secondary_url, f"/lookup/id/{query}")
        if data: return data

    # B. Symbol Lookup (Strictly using the selected species)
    data = fetch_api(primary_url, f"/lookup/symbol/{species}/{query}")
    if data: return data

    # C. Xref Search (Deep search for synonyms)
    xrefs = fetch_api(primary_url, f"/xrefs/symbol/{species}/{query}")
    if xrefs:
        for item in xrefs:
            if item.get("type") == "gene":
                lookup_data = fetch_api(primary_url, f"/lookup/id/{item.get('id')}")
                # Safety check to ensure we didn't jump to a different species (like a shark)
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
    selected_label = st.selectbox("Search Target:", list(SERVER_OPTIONS.keys()))
    user_url = SERVER_OPTIONS[selected_label]

    st.markdown("---")
    try:
        api_key = st.secrets["GEMINI_API_KEY"]
        st.success("🤖 AI Active")
    except:
        api_key = st.text_input("Gemini API Key:", type="password")

    ai_enabled = st.checkbox("Enable AI Analysis", value=True)
    st.caption("Designed by Maher, MATE-GBI")

# --- 4. Main UI ---

st.title("🧬 Gene Explorer")

species_input = st.text_input("Species Name:", value="triticum_aestivum")

tab1, tab2 = st.tabs(["🔍 Single Lookup", "🔗 Relationship Analysis"])

with tab1:
    c1, c2 = st.columns([1, 2])
    with c1:
        query_input = st.text_input("Gene Symbol or ID:", placeholder="e.g. TraesCS3B02G591300")
        search_btn = st.button("Fetch Data", use_container_width=True)

    if search_btn and query_input:
        with st.spinner("Searching Ensembl..."):
            data = smart_lookup(user_url, species_input, query_input)
        
        if data:
            st.success(f"✅ Result Found!")
            st.markdown(f"### {data.get('display_name', query_input)}")
            st.markdown(f"**Description:** {data.get('description', 'N/A')}")
            
            res_c1, res_c2 = st.columns(2)
            with res_c1:
                st.write(f"**Species:** `{data.get('species')}`")
                st.write(f"**Ensembl ID:** `{data.get('id')}`")
            with res_c2:
                st.write(f"**Location:** {data.get('seq_region_name')}:{data.get('start')}-{data.get('end')}")
                st.write(f"**Assembly:** {data.get('assembly_name')}")

            if ai_enabled and api_key:
                with st.spinner("AI Analysis..."):
                    prompt = f"Provide a concise, scientific 3-bullet summary for this gene: {json.dumps(data)}"
                    st.info(f"**🤖 AI Insights:**\n\n{call_gemini(api_key, prompt)}")
        else:
            st.error(f"❌ '{query_input}' not found. Note: Wheat IDs (Traes...) live on the Main server.")

with tab2:
    st.subheader("Multi-Gene Relationship Analysis")
    gene_list_raw = st.text_area("List (commas):", placeholder="TraesCS3B02G591300, TraesCS3D02G520300")
    analyze_btn = st.button("Analyze Connections")

    if analyze_btn and gene_list_raw:
        if not api_key:
            st.error("API Key Required.")
        else:
            genes = [g.strip() for g in gene_list_raw.replace("\n", ",").split(",") if g.strip()]
            collected_data = []
            pbar = st.progress(0)
            for i, g in enumerate(genes):
                d = smart_lookup(user_url, species_input, g)
                if d:
                    collected_data.append({"symbol": d.get('display_name'), "desc": d.get('description')})
                pbar.progress((i + 1) / len(genes))
            
            if collected_data:
                prompt = f"Analyze relationships concisely: {json.dumps(collected_data)}. No intros."
                st.markdown("### --- ANALYSIS ---")
                st.write(call_gemini(api_key, prompt))