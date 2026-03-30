import streamlit as st
import requests
import json
import re
from google import genai

# --- 1. CONFIGURATION ---
st.set_page_config(page_title="MATE-GBI Universal Gene Explorer", layout="wide")

MAIN_SERVER    = "https://rest.ensembl.org"
GENOMES_SERVER = "https://rest.ensemblgenomes.org"

KINGDOMS = {
    "Vertebrates": MAIN_SERVER,
    "Plants":      GENOMES_SERVER,
    "Bacteria":    GENOMES_SERVER,
    "Fungi":       GENOMES_SERVER,
}

HEADERS    = {"Content-Type": "application/json", "Accept": "application/json"}
MODEL_NAME = "gemini-2.5-flash"

# --- 2. THE SEARCH ENGINE ---

def fetch(base_url, endpoint, debug_log=None):
    """Single HTTP GET. 5s timeout so failed calls fail fast."""
    url = f"{base_url}{endpoint}"
    try:
        r = requests.get(url, headers=HEADERS, timeout=5)
        if debug_log is not None:
            debug_log.append(f"[{r.status_code}] {url}")
        if r.status_code == 200:
            return r.json()
    except Exception as e:
        if debug_log is not None:
            debug_log.append(f"[ERR] {url} -> {e}")
    return None


@st.cache_data(ttl=3600, show_spinner=False)
def resolve_exact_species(base_url, species_query):
    """
    Map a loose species name to the server's exact internal slug.
    e.g. 'hordeum_vulgare' -> 'hordeum_vulgare_goldenpromise'
    Cached for 1 hour — free on repeated searches within the same session.
    """
    res = fetch(base_url, f"/info/species?name={species_query}")
    if res and "species" in res and len(res["species"]) > 0:
        candidates = res["species"]
        # Prefer exact match, then slug that starts with the query (versioned slugs)
        for c in candidates:
            if c["name"] == species_query:
                return c["name"]
        for c in candidates:
            if c["name"].startswith(species_query):
                return c["name"]
        return candidates[0]["name"]
    return species_query


def _case_variants(query):
    """
    Build a short list of name variants to try, purely from the query itself.
    No hardcoded species prefixes — works for any organism.

    The dynamic prefix detection handles the convention used across ALL kingdoms
    where gene names carry a 2-3 letter species code (HvHox1, OsFLC, AtCBF,
    BnaA, MtDef, etc.).  The regex detects the boundary between the prefix and
    the gene name without needing to know the species in advance.
    """
    variants = [
        query,              # original as typed — most likely to hit, goes first
        query.capitalize(), # e.g. hox1 -> Hox1
        query.upper(),      # e.g. Hox1 -> HOX1
        query.lower(),      # e.g. HOX1 -> hox1
    ]

    # Dynamically detect a 2-3 letter species prefix before an uppercase letter:
    # HvHox1  -> prefix "Hv", gene "Hox1"
    # OsMADS1 -> prefix "Os", gene "MADS1"
    # AtFLC   -> prefix "At", gene "FLC"
    # b0344   -> no match (starts with lowercase, no uppercase boundary)
    m = re.match(r'^([A-Za-z]{2,3})([A-Z].+)$', query)
    if m:
        variants.append(m.group(2))            # strip prefix: HvHox1 -> Hox1
        variants.append(m.group(2).upper())    # also try all-caps: HOX1
        variants.append(m.group(2).lower())    # also try lower: hox1

    # Deduplicate while preserving priority order
    seen = set()
    return [v for v in variants if not (v in seen or seen.add(v))]


def _xref_search(base_url, species_slug, query, debug_log):
    """Xref/symbol search — exits immediately on first gene hit."""
    for variant in _case_variants(query):
        xrefs = fetch(base_url, f"/xrefs/symbol/{species_slug}/{variant}", debug_log)
        if xrefs:
            for item in xrefs:
                if item.get("type") == "gene":
                    data = fetch(base_url, f"/lookup/id/{item['id']}", debug_log)
                    if data:
                        return data
    return None


def _symbol_search(base_url, species_slug, query, debug_log):
    """lookup/symbol search — exits immediately on first hit."""
    for variant in _case_variants(query):
        data = fetch(base_url, f"/lookup/symbol/{species_slug}/{variant}", debug_log)
        if data:
            return data
    return None


def smart_lookup(kingdom, species, query, debug_log=None):
    if not query or not species:
        return None

    query         = query.strip()
    # Server is determined purely by the kingdom the user selected.
    # No species-name sniffing, no hardcoded gene-prefix routing.
    base_url      = KINGDOMS[kingdom]
    species_clean = species.strip().lower()

    # resolve_exact_species is cached — free after the first call per species
    resolved_sp   = resolve_exact_species(base_url, species_clean)
    sp_candidates = list(dict.fromkeys([resolved_sp, species_clean]))

    if debug_log is not None:
        debug_log.append(f"Server: {base_url}")
        debug_log.append(f"Species candidates: {sp_candidates}")
        debug_log.append(f"Variants: {_case_variants(query)}")

    # ── Step 1: Direct stable-ID lookup ──────────────────────────────────────
    # All Ensembl stable IDs share the pattern: letters then 11 digits,
    # but we also catch shorter accession-like strings (b0344, AT1G01010, etc.)
    # by checking for uppercase-letters + digits with no intervening lowercase.
    # A single fast call; a 404 costs almost nothing.
    looks_like_id = bool(re.match(r'^[A-Za-z]{1,6}\d', query)) and query == query.replace(" ", "")
    if looks_like_id:
        data = fetch(base_url, f"/lookup/id/{query}", debug_log)
        if data:
            return data

    # ── Step 2: Xref / synonym search ────────────────────────────────────────
    for sp in sp_candidates:
        data = _xref_search(base_url, sp, query, debug_log)
        if data:
            return data

    # ── Step 3: Direct symbol lookup ─────────────────────────────────────────
    for sp in sp_candidates:
        data = _symbol_search(base_url, sp, query, debug_log)
        if data:
            return data

    # ── Step 4: Cross-server fallback ────────────────────────────────────────
    # Try the other Ensembl server as a last resort.
    # This catches edge cases (e.g. a vertebrate gene that also has an entry
    # in EnsemblGenomes, or a plant gene accidentally routed to the wrong server).
    # Skipped if both servers are the same (Plants/Bacteria/Fungi all map to
    # GENOMES_SERVER, so there is no "other" server to try for those kingdoms
    # unless it would be the vertebrate one — which we do try for completeness).
    alt_url = GENOMES_SERVER if base_url == MAIN_SERVER else MAIN_SERVER
    if debug_log is not None:
        debug_log.append(f"Cross-server fallback: {alt_url}")

    alt_sp = resolve_exact_species(alt_url, species_clean)
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
    "Species Name (e.g. homo_sapiens, arabidopsis_thaliana, escherichia_coli, danio_rerio):",
    value="hordeum_vulgare"
)

tab1, tab2 = st.tabs(["🔍 Single Lookup", "🔗 Relationship Analysis"])

# --- TAB 1: SINGLE LOOKUP ---
with tab1:
    c1, c2 = st.columns([1, 2])
    with c1:
        query_input = st.text_input("Gene Symbol or ID:", placeholder="e.g. BRCA1, FLC, lacZ, TP53")
        search_btn  = st.button("Deep Search", use_container_width=True)

    if search_btn and query_input:
        debug_log = [] if debug_mode else None

        with st.spinner(f"Searching for '{query_input}'..."):
            data = smart_lookup(selected_kingdom, species_input, query_input, debug_log)

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
                "- Enable **Debug Mode** in the sidebar to see every URL tried.\n"
                "- Make sure the correct Kingdom is selected.\n"
                "- Check the exact species slug at [Ensembl](https://www.ensembl.org) "
                "or [EnsemblGenomes](https://plants.ensembl.org).\n"
                "- Try the gene's official symbol if a common name was used."
            )

# --- TAB 2: RELATIONSHIP ANALYSIS ---
with tab2:
    st.subheader("Concise Multi-Gene Relationship Analysis")
    gene_list_raw = st.text_area("Gene List (commas):", placeholder="BRCA1, TP53, EGFR")
    analyze_btn   = st.button("Analyze Connections", use_container_width=True)

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
                    collected_data.append({"symbol": d.get("display_name"), "desc": d.get("description")})
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