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
    Robustly maps ANY species input to the server's exact internal slug.

    Handles three kinds of input:
      - Slug:        'oryctolagus_cuniculus'  -> exact match on name field
      - Common name: 'rabbit'                 -> match on common_name / aliases
      - Partial:     'homo'                   -> match on slug prefix

    Priority order (stops at first match):
      1. Exact slug match          e.g. 'homo_sapiens' == 'homo_sapiens'
      2. Common name match         e.g. 'rabbit' in common_name 'rabbit'
      3. Alias match               e.g. 'mouse' in aliases list
      4. Slug starts-with match    e.g. 'hordeum' -> 'hordeum_vulgare_...'
      5. Fallback: original query  (don't blindly use candidates[0])

    Returning the original query on failure is safer than returning a random
    species — the gene search will then fail cleanly rather than silently
    searching the wrong organism.
    """
    res = fetch(base_url, f"/info/species?name={species_query}")
    if not res or "species" not in res or len(res["species"]) == 0:
        return species_query

    candidates = res["species"]
    q = species_query.lower().strip()

    # 1. Exact slug match
    for c in candidates:
        if c.get("name", "").lower() == q:
            return c["name"]

    # 2. Common name match (case-insensitive)
    for c in candidates:
        common = c.get("common_name", "") or ""
        if common.lower() == q:
            return c["name"]

    # 3. Alias match — Ensembl returns aliases as a list
    for c in candidates:
        aliases = c.get("aliases", []) or []
        if any(a.lower() == q for a in aliases):
            return c["name"]

    # 4. Slug starts-with (catches versioned slugs like hordeum_vulgare_goldenpromise)
    for c in candidates:
        if c.get("name", "").startswith(q):
            return c["name"]

    # 5. Partial common name match (e.g. "domestic rabbit" contains "rabbit")
    for c in candidates:
        common = c.get("common_name", "") or ""
        if q in common.lower():
            return c["name"]

    # No confident match — return original so the search fails transparently
    return species_query


@st.cache_data(ttl=3600, show_spinner=False)
def get_species_display_info(base_url, species_query):
    """
    Returns (resolved_slug, common_name, assembly) for a species query.
    Used to show the user a confirmation of what was actually resolved.
    """
    res = fetch(base_url, f"/info/species?name={species_query}")
    if not res or "species" not in res or len(res["species"]) == 0:
        return species_query, None, None

    candidates = res["species"]
    q = species_query.lower().strip()

    # Same priority logic as resolve_exact_species
    def score(c):
        name   = c.get("name", "").lower()
        common = (c.get("common_name", "") or "").lower()
        aliases = [a.lower() for a in (c.get("aliases", []) or [])]
        if name == q:             return 0
        if common == q:           return 1
        if q in aliases:          return 2
        if name.startswith(q):    return 3
        if q in common:           return 4
        return 99

    best = min(candidates, key=score)
    if score(best) == 99:
        return species_query, None, None

    return (
        best.get("name", species_query),
        best.get("common_name") or best.get("display_name"),
        best.get("assembly"),
    )


def _case_variants(query):
    """
    Build a short list of name variants from the query alone.
    Dynamically detects 2-3 letter species prefix (HvHox1, AtFLC, OsMADS).
    """
    variants = [
        query,
        query.capitalize(),
        query.upper(),
        query.lower(),
    ]
    m = re.match(r'^([A-Za-z]{2,3})([A-Z].+)$', query)
    if m:
        variants.append(m.group(2))
        variants.append(m.group(2).upper())
        variants.append(m.group(2).lower())
    seen = set()
    return [v for v in variants if not (v in seen or seen.add(v))]


def _xref_search(base_url, species_slug, query, debug_log):
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
    for variant in _case_variants(query):
        data = fetch(base_url, f"/lookup/symbol/{species_slug}/{variant}", debug_log)
        if data:
            return data
    return None


def smart_lookup(kingdom, species, query, debug_log=None):
    if not query or not species:
        return None

    query         = query.strip()
    base_url      = KINGDOMS[kingdom]
    species_clean = species.strip().lower()

    resolved_sp   = resolve_exact_species(base_url, species_clean)
    sp_candidates = list(dict.fromkeys([resolved_sp, species_clean]))

    if debug_log is not None:
        debug_log.append(f"Server: {base_url}")
        debug_log.append(f"Species candidates: {sp_candidates}")
        debug_log.append(f"Variants: {_case_variants(query)}")

    # Step 1: Direct stable-ID lookup
    looks_like_id = bool(re.match(r'^[A-Za-z]{1,6}\d', query)) and " " not in query
    if looks_like_id:
        data = fetch(base_url, f"/lookup/id/{query}", debug_log)
        if data:
            return data

    # Step 2: Xref / synonym search
    for sp in sp_candidates:
        data = _xref_search(base_url, sp, query, debug_log)
        if data:
            return data

    # Step 3: Direct symbol lookup
    for sp in sp_candidates:
        data = _symbol_search(base_url, sp, query, debug_log)
        if data:
            return data

    # Step 4: Cross-server fallback
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
    st.caption("Hungarian University of Agriculture and Life Science")

# --- MAIN UI ---
st.title("🧬 Universal Gene Explorer")
st.info(f"Connected to **{selected_kingdom}** Division")

species_input = st.text_input(
    "Species (scientific name or common name — e.g. homo_sapiens, rabbit, arabidopsis_thaliana):",
    value="hordeum_vulgare"
)

# ── Species resolution preview ───────────────────────────────────────────────
# Show the user exactly which species was resolved before they search,
# so a wrong match is visible immediately rather than after a failed gene lookup.
if species_input.strip():
    base_url_preview = KINGDOMS[selected_kingdom]
    resolved_slug, common_name, assembly = get_species_display_info(
        base_url_preview, species_input.strip().lower()
    )
    if resolved_slug != species_input.strip().lower() or common_name:
        label_parts = [f"`{resolved_slug}`"]
        if common_name:
            label_parts.append(common_name.title())
        if assembly:
            label_parts.append(f"assembly: {assembly}")
        st.caption(f"Resolved to: {' — '.join(label_parts)}")
    elif resolved_slug == species_input.strip().lower():
        st.caption(f"Species recognised: `{resolved_slug}`")
    else:
        st.warning(f"Species '{species_input}' not found on this server. Check spelling or try the scientific name.")

# ── Bacteria usage note ──────────────────────────────────────────────────────
if selected_kingdom == "Bacteria":
    st.info(
        "**Searching in Bacteria?** Two things to know:\n\n"
        "**Species:** Use the full strain slug, not just genus/species. "
        "EnsemblGenomes stores bacteria by assembly strain. Examples:\n"
        "- `escherichia_coli_str_k_12_substr_mg1655_gca_000005845`\n"
        "- `bacillus_subtilis_subsp_subtilis_str_168_gca_000009045`\n\n"
        "**Genes:** Bacteria use **locus tags**, not gene symbols. "
        "Gene names like `comK` or `lacZ` are rarely indexed — use the locus tag instead:\n"
        "- E. coli: `b0344` *(lacZ)*, `b0734` *(recA)*\n"
        "- B. subtilis: `BSU24220` *(spo0A)*, `BSU26890` *(comK)*"
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
            # Build Ensembl link from species + gene ID
            ENSEMBL_SUBDOMAIN = {
                "Vertebrates": "www",
                "Plants":      "plants",
                "Fungi":       "fungi",
                "Bacteria":    "bacteria",
            }
            subdomain   = ENSEMBL_SUBDOMAIN.get(selected_kingdom, "www")
            species_url = data.get("species", "").replace(" ", "_").capitalize()
            gene_id_url = data.get("id", "")
            ensembl_url = f"https://{subdomain}.ensembl.org/{species_url}/Gene/Summary?g={gene_id_url}"

            st.success("✅ Gene Found!")
            st.markdown(f"### {data.get('display_name', query_input)}")
            st.write(f"**Description:** {data.get('description', 'No description found in Ensembl.')}")
            st.markdown(f'<a href="{ensembl_url}" target="_blank"><img src="https://www.ensembl.org/favicon.ico" width="16" style="vertical-align:middle; margin-right:6px;"/>View on Ensembl</a>', unsafe_allow_html=True)

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
                rows = []
                for k, v in data.items():
                    if isinstance(v, dict):
                        display_v = json.dumps(v)
                    elif isinstance(v, list):
                        display_v = ", ".join(str(i) for i in v) if v else "—"
                    elif v is None or v == "":
                        display_v = "—"
                    else:
                        display_v = str(v)
                    rows.append({"Field": k, "Value": display_v})
                st.table(rows)
        else:
            st.error(f"❌ '{query_input}' not found in '{species_input}'.")
            st.warning(
                "**Troubleshooting tips:**\n"
                "- Check the species preview above — if the wrong species was resolved, "
                "use the full scientific name (e.g. `oryctolagus_cuniculus` instead of `rabbit`).\n"
                "- Enable **Debug Mode** in the sidebar to see every URL tried.\n"
                "- Make sure the correct Kingdom is selected.\n"
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