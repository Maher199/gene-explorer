import streamlit as st
import requests
import json
import re
from google import genai

# --- 1. Configuration ---
st.set_page_config(page_title="MATE-GBI Gene Explorer", layout="wide")

# Separated Servers - User has total control
SERVERS = {
    "Vertebrates & Wheat (rest.ensembl.org)": "https://rest.ensembl.org",
    "Plants - Rice/Maize/etc. (rest.ensemblgenomes.org)": "https://rest.ensemblgenomes.org",
    "Bacteria (rest.ensemblgenomes.org)": "https://rest.ensemblgenomes.org",
    "Fungi (rest.ensemblgenomes.org)": "https://rest.ensemblgenomes.org"
}

HEADERS = {"Content-Type": "application/json", "Accept": "application/json"}
MODEL_NAME = "gemini-3.1-flash-preview"

# --- 2. Logic Functions ---

def fetch_api(base_url, endpoint):
    """Standardized API communication."""
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
    Strict Search Protocol:
    1. Direct ID Lookup (If format matches)
    2. Direct Symbol Lookup (Locked to species)
    3. Xref Symbol Search (Locked to species - prevents 'Shark' errors)
    """
    if not query or not species: return None
    
    # Clean inputs
    query = query.strip()
    species = species.strip().lower()
    
    # ID Detection (ENS..., Traes..., Os..., etc.)
    is_id = bool(re.match(r"^(ENS[A-Z]*G|Traes|Os|AT|MLOC|HORVU|Zm|Sobic|b\d{4})\d+", query, re.I))
    
    # A. Search by ID
    if is_id:
        data = fetch_api(base_url, f"/lookup/id/{query}")
        # Only return if the species matches (safety check)
        if data and data.get('species', '').lower() == species:
            return data
        elif data and not is_id: # If it wasn't an ID but we found something, skip it
            pass

    # B. Search by Symbol
    data = fetch_api(base_url, f"/lookup/symbol/{species}/{query}")
    if data: return data

    # C. Deep Xref Search (For synonyms like Rht-B1 or HSP101c)
    xrefs = fetch_api(base_url, f"/xrefs/symbol/{species}/{query}")
    if xrefs:
        for item in xrefs:
            # We filter the list of results to find a GENE that matches the EXACT species requested
            if item.get("type") == "gene":
                potential_id = item.get("id")
                lookup_data = fetch_api(base_url, f"/lookup/id/{potential_id}")
                if lookup_data and lookup_data.get('species', '').lower() == species:
                    return lookup_data
                    
    return None

def call_gemini(api_key, prompt):
    """Concise AI Synthesis."""
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
    
    st.markdown("### 🖥️ Database Connection")
    selected_server_label = st.selectbox("Select Target Server:", list(SERVERS.keys()))
    current_base_url = SERVERS[selected_server_label]
    
    if "Vertebrates" in selected_server_label:
        st.info("💡 Use this server for **Human** and **Bread Wheat**.")
    else:
        st.info("💡 Use this server for **Rice, Bacteria, and Fungi**.")

    st.markdown("---")
    try:
        api_key = st.secrets["GEMINI_API_KEY"]
        st.success("🤖 AI Engine: Active")
    except:
        api_key = st.text_input("Gemini API Key:", type="password")

    ai_enabled = st.checkbox("Enable AI Summary", value=True)
    st.markdown("---")
    st.caption("Designed by Maher, MATE-GBI")

# --- 4. Main Interface ---

st.title("🧬 Universal Gene Explorer")
species_input = st.text_input("Species (lowercase_underscore):", value="triticum_aestivum")

tab1, tab2 = st.tabs(["🔍 Single Lookup", "🔗 Relationship Analysis"])

# --- TAB 1: SINGLE LOOKUP ---
with tab1:
    c1, c2 = st.columns([1, 2])
    with c1:
        query_input = st.text_input("Gene Symbol or ID:", placeholder="e.g. Rht-B1, TraesCS3B02G591300")
        search_btn = st.button("Search", use_container_width=True)

    if search_btn and query_input:
        with st.spinner(f"Querying {selected_server_label}..."):
            data = smart_lookup(current_base_url, species_input, query_input)
        
        if data:
            st.success(f"✅ Found: {data.get('display_name', query_input)}")
            st.markdown(f"**Description:** {data.get('description', 'N/A')}")
            
            res_c1, res_c2 = st.columns(2)
            with res_c1:
                st.write(f"**Species:** `{data.get('species')}`")
                st.write(f"**ID:** `{data.get('id')}`")
                st.write(f"**Biotype:** {data.get('biotype')}")
            with res_c2:
                st.write(f"**Chromosome:** {data.get('seq_region_name')}")
                st.write(f"**Position:** {data.get('start')}-{data.get('end')}")
                st.write(f"**Assembly:** {data.get('assembly_name')}")

            if ai_enabled and api_key:
                with st.spinner("AI Summarizing..."):
                    prompt = f"Provide a concise, scientific 3-bullet point summary for this gene: {json.dumps(data)}"
                    st.info(f"**🤖 AI Analysis:**\n\n{call_gemini(api_key, prompt)}")
            
            with st.expander("View Raw API Response"):
                st.json(data)
        else:
            st.error(f"❌ '{query_input}' not found in '{species_input}' on the selected server.")
            st.warning("Ensure the 'Target Server' in the sidebar matches your organism (e.g., Wheat is on the Vertebrates server).")

# --- TAB 2: RELATIONSHIP ANALYSIS ---
with tab2:
    st.subheader("Concise Multi-Gene Relationship Analysis")
    gene_list_raw = st.text_area("Gene List (commas):", placeholder="BRCA1, BRCA2, ATM")
    analyze_btn = st.button("Analyze Connections", use_container_width=True)

    if analyze_btn and gene_list_raw:
        if not api_key:
            st.error("API Key Required.")
        else:
            genes = [g.strip() for g in gene_list_raw.replace("\n", ",").split(",") if g.strip()]
            collected_data = []
            pbar = st.progress(0)
            for i, g in enumerate(genes):
                d = smart_lookup(current_base_url, species_input, g)
                if d:
                    collected_data.append({"symbol": d.get('display_name'), "desc": d.get('description')})
                pbar.progress((i + 1) / len(genes))
            
            if collected_data:
                with st.spinner("AI Synthesizing..."):
                    prompt = f"Analyze biological relationships concisely: {json.dumps(collected_data)}. Focus on pathways. No intros."
                    st.markdown("### --- ANALYSIS ---")
                    st.write(call_gemini(api_key, prompt))
            else:
                st.error("Could not find any genes from the list on this server.")