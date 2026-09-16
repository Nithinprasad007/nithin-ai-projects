import re
import io
import requests
import streamlit as st
from pypdf import PdfReader
from docx import Document

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
OLLAMA_HOST_DEFAULT = "http://localhost:11434"
MAX_FILE_SIZE_MB = 10

st.set_page_config(page_title="📄 Resume & Job Matcher", layout="centered")
st.title("📄 Resume & Job Matcher")

st.sidebar.header("⚙️ Settings")

ollama_host = st.sidebar.text_input(
    "Ollama host",
    value=OLLAMA_HOST_DEFAULT,
    help="Change this if Ollama is running on a different host/port.",
)


@st.cache_data(ttl=30, show_spinner=False)
def get_available_models(host: str):
    """Query Ollama for installed models. Cached briefly so the sidebar stays fast."""
    try:
        resp = requests.get(f"{host}/api/tags", timeout=5)
        resp.raise_for_status()
        models = [m["name"] for m in resp.json().get("models", [])]
        return models, None
    except requests.exceptions.ConnectionError:
        return [], "connection_error"
    except Exception as e:
        return [], str(e)


models, err = get_available_models(ollama_host)

if err == "connection_error":
    st.sidebar.error("⚠️ Can't reach Ollama. Is `ollama serve` running?")
    selected_model = st.sidebar.text_input("Model name (manual)", value="llama3")
elif err:
    st.sidebar.error(f"⚠️ Error fetching models: {err}")
    selected_model = st.sidebar.text_input("Model name (manual)", value="llama3")
elif not models:
    st.sidebar.warning("No models found. Run `ollama pull llama3` first.")
    selected_model = st.sidebar.text_input("Model name (manual)", value="llama3")
else:
    selected_model = st.sidebar.selectbox("Model", options=models)

st.sidebar.divider()
st.sidebar.info(
    """
**How this works**
1. Install [Ollama](https://ollama.ai)
2. `ollama serve` in one terminal
3. `ollama pull llama3` (or any model) in another
4. Upload a Resume + Job Description below
"""
)

# ---------------------------------------------------------------------------
# File parsing helpers
# ---------------------------------------------------------------------------


@st.cache_data(show_spinner=False)
def extract_pdf_text(file_bytes: bytes) -> str:
    reader = PdfReader(io.BytesIO(file_bytes))
    return "\n".join(page.extract_text() or "" for page in reader.pages)


@st.cache_data(show_spinner=False)
def extract_docx_text(file_bytes: bytes) -> str:
    doc = Document(io.BytesIO(file_bytes))
    return "\n".join(p.text for p in doc.paragraphs)


def get_text_from_file(uploaded_file) -> str:
    file_bytes = uploaded_file.getvalue()

    size_mb = len(file_bytes) / (1024 * 1024)
    if size_mb > MAX_FILE_SIZE_MB:
        raise ValueError(f"File too large ({size_mb:.1f} MB). Limit is {MAX_FILE_SIZE_MB} MB.")
    if len(file_bytes) == 0:
        raise ValueError("File is empty.")

    if uploaded_file.type == "application/pdf":
        text = extract_pdf_text(file_bytes)
    elif uploaded_file.type in (
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ):
        text = extract_docx_text(file_bytes)
    else:
        text = file_bytes.decode("utf-8", errors="ignore")

    if not text.strip():
        raise ValueError("No extractable text found in this file (it may be a scanned image PDF).")

    return text


REQUIREMENT_LINE_RE = re.compile(
    r"^\s*\d*[\.\)]?\s*(core|nice)\s*[-:]\s*(.+)$",
    re.IGNORECASE | re.MULTILINE,
)

STATUS_LINE_RE = re.compile(
    r"^\s*(\d+)[\.\):]?\s*[-:]?\s*(match|partial|missing)\b\s*[-:]?\s*(.*)$",
    re.IGNORECASE | re.MULTILINE,
)


def call_ollama(host: str, model: str, prompt: str, timeout: int = 90) -> str:
    """Single non-streaming call to Ollama's generate endpoint. Raises on HTTP/connection errors."""
    resp = requests.post(
        f"{host}/api/generate",
        json={"model": model, "prompt": prompt, "stream": False},
        timeout=timeout,
    )
    resp.raise_for_status()
    return resp.json().get("response", "")


def extract_requirements(host: str, model: str, job_text: str):
    """STEP 1: extract requirements from the job description ONLY.
    Deliberately never shown the resume, so the model cannot confuse the two documents
    (this is what caused the original bug where resume sentences were echoed back as
    'requirements' and trivially matched against themselves).
    """
    prompt = f"""You are analyzing a job description to extract its key requirements.

Job Description:
{job_text}

List the 6-10 most important requirements from this job description: required skills,
tools, years of experience, domain knowledge, and core responsibilities. Tag each one as
CORE (essential to doing the job) or NICE (a bonus, not essential).

Output ONLY a list, one requirement per line, in exactly this format and nothing else:
CORE - <requirement>
NICE - <requirement>

Do not add commentary, headers, or numbering. Do not repeat the job description back."""

    raw = call_ollama(host, model, prompt)
    requirements = []
    for m in REQUIREMENT_LINE_RE.finditer(raw):
        requirements.append({"priority": m.group(1).strip().lower(), "text": m.group(2).strip()})
    return requirements, raw


def classify_against_resume(host: str, model: str, requirements: list, resume_text: str):
    """STEP 2: classify resume evidence against the FIXED requirement list from Step 1.
    The model can't invent, drop, or reword requirements here -- it only assigns a status
    to each numbered item, which keeps its output easy to parse reliably.
    """
    numbered = "\n".join(f"{i+1}. {r['text']}" for i, r in enumerate(requirements))

    prompt = f"""You are a skeptical, detail-oriented technical recruiter. Be accurate and
critical, not encouraging. Do not give credit for general competence or unrelated
achievements -- only count direct, explicit evidence in the resume text below.

Resume:
{resume_text}

For EACH numbered requirement below, decide whether the resume shows:
- MATCH: clear, direct evidence
- PARTIAL: adjacent/transferable evidence only, not a direct match
- MISSING: no evidence at all

Requirements:
{numbered}

Respond with EXACTLY one line per requirement, in this format and nothing else:
1: MATCH - <one short reason>
2: MISSING - <one short reason>

Keep the same numbering as above. Do not skip any, do not add extra ones, do not add
headers or commentary."""

    raw = call_ollama(host, model, prompt)
    statuses = {}
    for m in STATUS_LINE_RE.finditer(raw):
        idx = int(m.group(1))
        statuses[idx] = {"status": m.group(2).strip().lower(), "evidence": m.group(3).strip()}

    rows = []
    for i, r in enumerate(requirements):
        s = statuses.get(i + 1)
        rows.append(
            {
                "requirement": r["text"],
                "priority": r["priority"],
                "status": s["status"] if s else "unparsed",
                "evidence": s["evidence"] if s else "(model didn't return a status for this item)",
            }
        )
    return rows, raw


def generate_summary(host: str, model: str, rows: list, resume_text: str, job_text: str) -> str:
    """STEP 3: free-text strengths + recommendations, grounded in the already-computed rows
    so the model can't quietly redo the scoring itself.
    """
    rows_text = "\n".join(f"- [{r['priority'].upper()}] {r['requirement']}: {r['status'].upper()} ({r['evidence']})" for r in rows)

    prompt = f"""Based on this requirement-by-requirement evidence analysis:

{rows_text}

Write two short sections in Markdown:
### Key Strengths
Only genuinely relevant strengths that align with MATCH or PARTIAL items above. Do not pad
this with unrelated resume achievements.

### Recommendations
Specific, actionable suggestions to close the gaps on MISSING items above, tailored to this
role.

Keep both sections concise. Do not include a numeric score -- that is calculated separately."""

    return call_ollama(host, model, prompt)


def calculate_fit_score(rows: list):
    """Deterministically compute a 0-100 fit score from parsed requirement rows.

    This deliberately does NOT let the LLM self-report a percentage — small local
    models are prone to being generous/sycophantic about scores. Instead we score
    purely from the structured MATCH/PARTIAL/MISSING evidence it extracted, so the
    result is consistent and auditable regardless of role or resume.
    """
    core = [r for r in rows if r["priority"] == "core" and r["status"] in ("match", "partial", "missing")]
    nice = [r for r in rows if r["priority"] == "nice" and r["status"] in ("match", "partial", "missing")]

    def bucket_score(items):
        if not items:
            return None
        points = sum(1.0 if r["status"] == "match" else 0.5 if r["status"] == "partial" else 0.0 for r in items)
        return points / len(items)

    core_ratio = bucket_score(core)
    nice_ratio = bucket_score(nice)

    if core_ratio is None:
        return None  # couldn't parse anything usable

    if nice_ratio is not None:
        raw_score = core_ratio * 80 + nice_ratio * 20
    else:
        raw_score = core_ratio * 100

    # Hard clamp: if most CORE requirements are missing, the role is fundamentally
    # not a match, no matter how strong the resume looks elsewhere.
    core_missing_ratio = sum(1 for r in core if r["status"] == "missing") / len(core) if core else 0
    if core_missing_ratio > 0.5:
        raw_score = min(raw_score, 30)

    return round(raw_score)


# ---------------------------------------------------------------------------
# UI
# ---------------------------------------------------------------------------

col1, col2 = st.columns(2)
with col1:
    resume_file = st.file_uploader("Resume (PDF / DOCX / TXT)", type=["pdf", "docx", "txt"])
with col2:
    job_file = st.file_uploader("Job Description (PDF / DOCX / TXT)", type=["pdf", "docx", "txt"])

if st.button("🔍 Match Resume with Job Description", type="primary"):
    if not (resume_file and job_file):
        st.warning("⚠️ Please upload both a resume and a job description.")
    else:
        try:
            resume_text = get_text_from_file(resume_file)
            job_text = get_text_from_file(job_file)
        except ValueError as e:
            st.error(f"⚠️ {e}")
            st.stop()

        try:
            with st.status("Analyzing job description...", expanded=False) as status:
                requirements, raw_req = extract_requirements(ollama_host, selected_model, job_text)
                if not requirements:
                    status.update(label="Couldn't extract requirements", state="error")
                    st.error(
                        "⚠️ Couldn't extract a requirements list from the job description. "
                        "The model may have ignored the format instructions. Try a stronger "
                        "model (e.g. `llama3.1:8b`, `mistral`, or `qwen2.5:14b`) or a clearer "
                        "job description."
                    )
                    with st.expander("Raw model output (for debugging)"):
                        st.text(raw_req)
                    st.stop()

                status.update(label=f"Found {len(requirements)} requirements — checking resume evidence...")
                rows, raw_status = classify_against_resume(ollama_host, selected_model, requirements, resume_text)

                unparsed = sum(1 for r in rows if r["status"] == "unparsed")
                if unparsed:
                    st.caption(
                        f"⚠️ {unparsed} of {len(rows)} requirements couldn't be classified from the "
                        "model's output and were excluded from scoring."
                    )

                status.update(label="Writing strengths and recommendations...")
                summary = generate_summary(ollama_host, selected_model, rows, resume_text, job_text)

                status.update(label="Done", state="complete")

            # --- Score (calculated in Python, not by the model) ---
            score = calculate_fit_score(rows)

            if score is not None:
                st.metric("Fit Score (calculated from evidence)", f"{score}%")
                st.progress(score / 100)
                core_rows = [r for r in rows if r["priority"] == "core"]
                core_missing = sum(1 for r in core_rows if r["status"] == "missing")
                if core_rows and core_missing > 0:
                    st.caption(f"⚠️ {core_missing} of {len(core_rows)} core requirements show no evidence in the resume.")
            else:
                st.warning("⚠️ Not enough classified requirements to calculate a score.")

            # --- Requirements table ---
            st.subheader("📋 Requirement-by-requirement breakdown")
            table_md = "| Requirement | Priority | Status | Evidence |\n|---|---|---|---|\n"
            for r in rows:
                icon = {"match": "✅", "partial": "🟡", "missing": "❌"}.get(r["status"], "❓")
                table_md += f"| {r['requirement']} | {r['priority'].upper()} | {icon} {r['status'].upper()} | {r['evidence']} |\n"
            st.markdown(table_md)

            # --- Strengths & recommendations ---
            st.subheader("📌 Strengths & Recommendations")
            st.markdown(summary)

            report = (
                f"# Resume Match Report\n\n**Fit Score: {score}%**\n\n"
                f"## Requirement Breakdown\n\n{table_md}\n\n{summary}\n"
            )
            st.session_state["resume_match"] = report

        except requests.exceptions.ConnectionError:
            st.error(
                "⚠️ Couldn't connect to Ollama. Make sure `ollama serve` is running "
                f"and reachable at `{ollama_host}`."
            )
        except requests.exceptions.Timeout:
            st.error("⚠️ The request to Ollama timed out. Try a smaller model or shorter documents.")
        except Exception as e:
            st.error(f"⚠️ An unexpected error occurred: {e}")

if "resume_match" in st.session_state:
    st.download_button(
        "💾 Download Match Report",
        st.session_state["resume_match"],
        file_name="resume_match_report.md",
        mime="text/markdown",
    )