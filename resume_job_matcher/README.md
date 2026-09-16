# Resume & Job Matcher

## 🚀 Overview
Upload a **Resume** and a **Job Description**, and this app uses a local LLM (via [Ollama](https://ollama.ai)) to give you an honest, evidence-based fit score — not a vibes-based guess.

Runs entirely locally — no data leaves your machine, and no API keys are required.

---

## 🛠️ Tech Stack
- **Python** + **Streamlit** — UI
- **Ollama** — local LLM inference (e.g. `llama3`, `mistral`, `qwen2.5`)
- **pypdf** — PDF text extraction
- **python-docx** — DOCX text extraction

---

## ⚡ Setup

```bash
# From inside this folder:
pip install -r requirements.txt

# Install and run Ollama: https://ollama.ai
ollama serve            # in one terminal
ollama pull llama3      # in another terminal

# Run the app
streamlit run app.py
```

---

## ✨ How it works — and why the score is trustworthy

Early versions of this app asked the LLM to read both documents and self-report a percentage in one shot. Small local models turned out to be **sycophantic** — they'd give a QA engineer an 85% match against a Sales Executive role just because the resume looked generally strong, without actually checking for sales-specific evidence.

The current pipeline fixes this with three separate, narrower LLM calls plus deterministic scoring:

1. **Extract requirements** — the model reads *only* the job description and produces a tagged list (`CORE` vs `NICE`) of what the role actually needs. It never sees the resume in this step, so it can't confuse the two documents.
2. **Classify evidence** — the model checks the resume against that fixed, numbered list and marks each item `MATCH` / `PARTIAL` / `MISSING`.
3. **Score — calculated in Python, not by the LLM.** Core requirements are worth 80% of the score, nice-to-haves 20%, partial matches get half credit. If more than half the core requirements are missing, the score is hard-capped at 30%, regardless of how strong the resume looks otherwise.
4. **Summary** — strengths and recommendations are generated last, grounded in the already-computed evidence table.

This means the score is reproducible and auditable: you can see exactly which requirements were checked and why each was marked match/partial/missing.

---

## ⚠️ Limitations
- Requires Ollama running locally — this is **not** a hosted/cloud service.
- Scanned/image-only PDFs won't extract text (no OCR).
- Score quality depends on the model's ability to follow the structured output format. Smaller models (e.g. `llama3` 8B) occasionally fail to format Step 1 or Step 2 correctly — the app will show a warning and the raw output if this happens. Larger instruction-tuned models (`llama3.1:8b`, `mistral`, `qwen2.5:14b`) are more reliable.
- Resume/job description text is sent to your local LLM only, but isn't encrypted at rest.

---

## 📄 License
See the root [LICENSE](../LICENSE) for this repository.

