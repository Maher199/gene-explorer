import streamlit as st
import requests
import json
import re
from google import genai

# --- 1. CONFIGURATION ---
st.set_page_config(page_title="MATE-GBI Universal Gene Explorer", layout="wide")

MAIN_SERVER = "https://rest.ensembl.org"
GENOMES_SERVER = "https://rest.ensemblgenomes.org"

KINGDOMS = {
    "Vertebrates": MAIN_SERVER,
    "Plants": GENOMES_SERVER,
    "Bacteria": GENOMES_SERVER,
    "Fungi": GENOMES_SERVER
}

HEADERS = {"Content-Type": "application/json", "Accept": "application/json"}
MODEL_NAME = "gemini-2.0-flash"

# --- 2. THE SEARCH ENGINE ---

def fetch(base_url, endpoint, debug_log=None):
    """Fetch with visible error logging for debugging."""
    url = f"{base_url}{endpoint}"
    try:
        r = requests.get(url, headers=HEADERS, timeout=12)
        if debug_log is not None:
            debug_log.append(f"[{r.status_code}] {url}")
        if r.status_code == 200:
            return r.json()
    except Exception as e:
        if debug_log is not None:
            debug_log.append(f"[ERR] {url} → {e}")
    return None


def resolve_exact_species(base_url, species_query, debug_log=None):
    """
    Resolves a loose species name to the server's exact slug.
    Prefers entries whose name STARTS WITH the query to avoid picking
    a completely different species (e.g. hordeum_vulgare_goldenpromise
    is fine, but not hordeum_spontaneum).
    Falls back to the original query if nothing matches.
    """
    res = fetch(base_url, f"/info/species?name={species_query}", debug_log)
    if res and "species" in res and len(res["species"]) > 0:
        candidates = res["species"]
        # Prefer the exact match first
        for c in candidates:
            if c["name"] == species_query:
                return c["name"]
        # Then prefer one that starts with the query (versioned slug)
        for c in candidates:
            if c["name"].startswith(species_query):
                return c["name"]
        # Fallback: just take the first
        return candidates[0]["name"]
    return species_query


def _case_variants(query):
    """Generate case variants to maximise xref/symbol hit rate."""
    variants = [
        query,                   # original:  Hox1
        query.upper(),           # all-caps:  HOX1
        query.lower(),           # all-lower: hox1
        query.capitalize(),      # title:     Hox1  (same as original if already title-case)
    ]
    # Also try with common species prefixes stripped/added for plant genes (HvHox1 → Hox1, etc.)
    prefixes = ["Hv", "Os", "Ta", "Zm", "At", "Sb"]
    for p in prefixes:
        if query.startswith(p):
            variants.append(query[len(p):])          # strip prefix
        else:
            variants.append(f"{p}{query}")           # add prefix
    # Deduplicate while preserving order
    seen = set()
    return [v for v in variants if not (v in seen or seen.add(v))]


def _xref_search(base_url, species_slug, query, debug_log):
    """Try xref/symbol endpoint across all case variants."""
    for variant in _case_variants(query):
        xrefs = fetch(base_url, f"/xrefs/symbol/{species_slug}/{variant}", debug_log)
        if xrefs:
            for item in xrefs:
                if item.get("type") == "gene":
                    gene_id = item.get("id")
                    data = fetch(base_url, f"/lookup/id/{gene_id}", debug_log)
                    if data:
                        return data
    return None


def _symbol_search(base_url, species_slug, query, debug_log):
    """Try lookup/symbol endpoint across all case variants."""
    for variant in _case_variants(query):
        data = fetch(base_url, f"/lookup/symbol/{species_slug}/{variant}", debug_log)
        if data:
            return data
    return None


def smart_lookup(kingdom, species, query, debug_log=None):
    if not query or not species:
        return None

    query = query.strip()
    is_wheat = bool(re.match(r"^Traes", query, re.I)) or "triticum_aestivum" in species.lower()
    base_url = MAIN_SERVER if (is_wheat or kingdom == "Vertebrates") else KINGDOMS[kingdom]

    species_clean = species.strip().lower()

    # Resolve the exact server-side slug; keep original as fallback
    resolved_sp = resolve_exact_species(base_url, species_clean, debug_log)
    # Build a list of species slugs to try (resolved first, then original)
    sp_candidates = list(dict.fromkeys([resolved_sp, species_clean]))  # deduplicated, ordered

    if debug_log is not None:
        debug_log.append(f"Species candidates: {sp_candidates}")

    # ── STEP 1: Direct ID lookup ──────────────────────────────────────────────
    is_id = bool(re.match(r"^(ENS|Traes|Os|AT|MLOC|HORVU|Zm|Sobic|b\d{4})", query, re.I))
    if is_id:
        data = fetch(base_url, f"/lookup/id/{query}", debug_log)
        if data:
            return data

    # ── STEP 2: Xref search (synonym-aware) – try each species slug ──────────
    for sp in sp_candidates:
        data = _xref_search(base_url, sp, query, debug_log)
        if data:
            return data

    # ── STEP 3: Direct symbol lookup – try each species slug ─────────────────
    for sp in sp_candidates:
        data = _symbol_search(base_url, sp, query, debug_log)
        if data:
            return data

    # ── STEP 4: Cross-server fallback (try the OTHER server) ─────────────────
    alt_url = GENOMES_SERVER if base_url == MAIN_SERVER else MAIN_SERVER
    if debug_log is not None:
        debug_log.append(f"Trying alternate server: {alt_url}")
    alt_sp = resolve_exact_species(alt_url, species_clean, debug_log)
    for sp in list(dict.fromkeys([alt_sp, species_clean])):
        data = _xref_search(alt_url, sp, query, debug_log)
        if data:
            return data
        data = _symbol_search(alt_url, sp, query, debug_log)
        if data:
            return data

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

    st.markdown("### 🖥️ Database Connection")
    selected_kingdom = st.radio("Target Kingdom:", list(KINGDOMS.keys()), index=1)

    st.markdown("---")
    try:
        api_key = st.secrets["GEMINI_API_KEY"]
        st.success("🤖 AI Engine: Active")
    except:
        api_key = st.text_input("Gemini API Key:", type="password")

    ai_enabled = st.checkbox("Enable AI Analysis", value=True)
    debug_mode = st.checkbox("🛠️ Debug Mode", value=False)
    st.caption("Designed by Maher, MATE-GBI")

# --- MAIN UI ---
st.title("🧬 Universal Gene Explorer")
st.info(f"Connected to **{selected_kingdom}** Division")

species_input = st.text_input(
    "Species Name (e.g. hordeum_vulgare, triticum_aestivum, escherichia_coli):",
    value="hordeum_vulgare"
)

tab1, tab2 = st.tabs(["🔍 Single Lookup", "🔗 Relationship Analysis"])

# --- TAB 1: SINGLE LOOKUP ---
with tab1:
    c1, c2 = st.columns([1, 2])
    with c1:
        query_input = st.text_input("Gene Symbol or ID:", placeholder="e.g. Hox1, Vrs1, b0344")
        search_btn = st.button("Deep Search", use_container_width=True)

    if search_btn and query_input:
        debug_log = [] if debug_mode else None

        with st.spinner(f"Running deep search for '{query_input}'..."):
            data = smart_lookup(selected_kingdom, species_input, query_input, debug_log)

        # Show debug trace regardless of success/failure
        if debug_mode and debug_log:
            with st.expander("🛠️ Debug Trace", expanded=not bool(data)):
                for line in debug_log:
                    st.code(line)

        if data:
            st.success("✅ Gene Found!")
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
            st.warning(
                "**Troubleshooting tips:**\n"
                "- Enable **Debug Mode** in the sidebar to see exactly which URLs were tried.\n"
                "- For Barley, make sure Kingdom is set to **Plants**.\n"
                "- Try alternate names: `Hox1` → `HvHox1`, `VRS1`; `Vrs1` → `HvVRS1`.\n"
                "- Check the species slug is correct on [EnsemblGenomes](https://plants.ensembl.org)."
            )

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
            not_found = []
            pbar = st.progress(0)
            for i, g in enumerate(genes):
                d = smart_lookup(selected_kingdom, species_input, g)
                if d:
                    collected_data.append({"symbol": d.get('display_name'), "desc": d.get('description')})
                else:
                    not_found.append(g)
                pbar.progress((i + 1) / len(genes))

            if not_found:
                st.warning(f"⚠️ Could not find: {', '.join(not_found)}")

            if collected_data:
                with st.spinner("AI Synthesis..."):
                    prompt = f"Analyze biological relationships concisely: {json.dumps(collected_data)}. No intros."
                    st.markdown("### --- CONCISE RELATIONSHIP ANALYSIS ---")
                    st.write(call_ai(api_key, prompt))
            else:
                st.error("No genes found for the provided list.")