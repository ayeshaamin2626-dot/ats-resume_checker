"""AI Resume ATS Checker — Streamlit + Google Gemini Flash.

Upload a resume (PDF, DOCX or TXT), optionally paste a job description, and get:
  * an overall ATS score (0-100) with a section-by-section breakdown
  * keyword matches / gaps
  * prioritised, actionable improvements
"""

import hashlib
import io
import json
import os
import re

import streamlit as st
from docx import Document
from google import genai
from google.genai import types
from pypdf import PdfReader

# --------------------------------------------------------------------------
# Config
# --------------------------------------------------------------------------
DEFAULT_MODEL = "gemini-3.8-flash"  # override with env var / secret GEMINI_MODEL
MAX_FILE_MB = 5
MAX_RESUME_CHARS = 30_000
MIN_RESUME_CHARS = 150

SCORE_SECTIONS = {
    "keywords": "Keywords & Skills Match",
    "formatting": "Formatting & ATS Readability",
    "experience": "Experience & Achievements",
    "education": "Education & Certifications",
    "summary": "Summary / Profile",
    "language": "Grammar & Clarity",
}

PROMPT = """You are an expert ATS (Applicant Tracking System) analyst and senior recruiter.
Evaluate the resume below{jd_clause}.

Scoring guidance:
- Be realistic and strict; average resumes score 55-70. Do not inflate.
- Reward: quantified achievements, strong action verbs, relevant keywords, clear
  standard section headings, consistent dates, concise bullet points, contact info.
- Penalise: missing sections, vague duties, no metrics, typos, keyword gaps, odd
  structure that suggests tables/columns/graphics, very long or very short content.
{jd_scoring}
Return ONLY a JSON object (no markdown, no commentary) with exactly this shape:
{{
  "ats_score": <integer 0-100>,
  "verdict": "<one short sentence summarising the resume's ATS readiness>",
  "section_scores": {{
    "keywords": <int 0-100>,
    "formatting": <int 0-100>,
    "experience": <int 0-100>,
    "education": <int 0-100>,
    "summary": <int 0-100>,
    "language": <int 0-100>
  }},
  "matched_keywords": [<up to 20 strings>],
  "missing_keywords": [<up to 20 strings>],
  "strengths": [<3-6 short strings>],
  "improvements": [
    {{
      "priority": "High" | "Medium" | "Low",
      "section": "<resume section>",
      "issue": "<what is wrong>",
      "suggestion": "<specific fix>",
      "example": "<optional rewritten example line, or empty string>"
    }}
  ]
}}
Give 6-10 improvements, most important first.

{jd_block}=== RESUME START ===
{resume}
=== RESUME END ===
"""


# --------------------------------------------------------------------------
# File parsing
# --------------------------------------------------------------------------
def extract_text(filename: str, data: bytes) -> str:
    """Extract plain text from a PDF, DOCX or TXT upload."""
    name = filename.lower()
    if name.endswith(".pdf"):
        reader = PdfReader(io.BytesIO(data))
        if reader.is_encrypted:
            try:
                reader.decrypt("")
            except Exception:
                raise ValueError("This PDF is password-protected.")
        return "\n".join((page.extract_text() or "") for page in reader.pages)
    if name.endswith(".docx"):
        doc = Document(io.BytesIO(data))
        parts = [p.text for p in doc.paragraphs]
        for table in doc.tables:
            for row in table.rows:
                parts.append(" | ".join(c.text for c in row.cells))
        return "\n".join(parts)
    if name.endswith(".txt"):
        return data.decode("utf-8", errors="ignore")
    raise ValueError("Unsupported file type. Please upload a PDF, DOCX or TXT file.")


def clean_text(text: str) -> str:
    text = text.replace("\x00", "")
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


# --------------------------------------------------------------------------
# Gemini
# --------------------------------------------------------------------------
def get_secret(name: str, default: str = "") -> str:
    """Read from env first, then Streamlit secrets (safe if no secrets file)."""
    value = os.environ.get(name)
    if value:
        return value
    try:
        return str(st.secrets[name])
    except Exception:
        return default


def parse_json(raw: str) -> dict:
    """Parse model output into a dict, tolerating code fences / stray text."""
    raw = (raw or "").strip()
    raw = re.sub(r"^```(?:json)?\s*|\s*```$", "", raw, flags=re.IGNORECASE)
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        start, end = raw.find("{"), raw.rfind("}")
        if start != -1 and end > start:
            return json.loads(raw[start : end + 1])
        raise


def _score(value, default=0) -> int:
    try:
        return max(0, min(100, int(round(float(value)))))
    except (TypeError, ValueError):
        return default


def _str_list(value, limit=30) -> list:
    if not isinstance(value, list):
        return []
    return [str(v).strip() for v in value if str(v).strip()][:limit]


def normalise(result: dict) -> dict:
    """Coerce the model's JSON into a safe, predictable structure."""
    sections = result.get("section_scores") or {}
    improvements = []
    for item in result.get("improvements") or []:
        if not isinstance(item, dict):
            continue
        priority = str(item.get("priority", "Medium")).strip().title()
        if priority not in ("High", "Medium", "Low"):
            priority = "Medium"
        improvements.append(
            {
                "priority": priority,
                "section": str(item.get("section", "General")).strip(),
                "issue": str(item.get("issue", "")).strip(),
                "suggestion": str(item.get("suggestion", "")).strip(),
                "example": str(item.get("example", "") or "").strip(),
            }
        )
    return {
        "ats_score": _score(result.get("ats_score")),
        "verdict": str(result.get("verdict", "")).strip(),
        "section_scores": {k: _score(sections.get(k)) for k in SCORE_SECTIONS},
        "matched_keywords": _str_list(result.get("matched_keywords")),
        "missing_keywords": _str_list(result.get("missing_keywords")),
        "strengths": _str_list(result.get("strengths"), 8),
        "improvements": improvements,
    }


def analyze_resume(resume_text: str, job_description: str, api_key: str, model: str) -> dict:
    jd = job_description.strip()
    prompt = PROMPT.format(
        jd_clause=" against the job description provided" if jd else " for general ATS readiness",
        jd_scoring=(
            "- A job description is provided: weigh keyword/skill alignment with it heavily and "
            "list missing_keywords from the job description.\n"
            if jd
            else "- No job description is provided: infer the most likely target role from the "
            "resume and judge keywords against typical expectations for that role.\n"
        ),
        jd_block=f"=== JOB DESCRIPTION START ===\n{jd[:8000]}\n=== JOB DESCRIPTION END ===\n\n"
        if jd
        else "",
        resume=resume_text[:MAX_RESUME_CHARS],
    )
    client = genai.Client(api_key=api_key)
    response = client.models.generate_content(
        model=model,
        contents=prompt,
        config=types.GenerateContentConfig(
            temperature=0.2,
            response_mime_type="application/json",
        ),
    )
    return normalise(parse_json(response.text))


@st.cache_data(show_spinner=False, ttl=3600)
def cached_analysis(resume_hash: str, resume_text: str, jd: str, model: str, key_hash: str, api_key: str):
    # resume_hash / key_hash are part of the cache key; api_key is not shown anywhere.
    return analyze_resume(resume_text, jd, api_key, model)


# --------------------------------------------------------------------------
# UI helpers
# --------------------------------------------------------------------------
def score_band(score: int):
    if score >= 80:
        return "Excellent", "🟢"
    if score >= 65:
        return "Good", "🟡"
    if score >= 50:
        return "Needs work", "🟠"
    return "Poor", "🔴"


def render_results(result: dict, has_jd: bool):
    score = result["ats_score"]
    label, icon = score_band(score)

    left, right = st.columns([1, 2])
    with left:
        st.metric("ATS Score", f"{score} / 100")
        st.progress(score / 100)
        st.markdown(f"### {icon} {label}")
    with right:
        if result["verdict"]:
            st.info(result["verdict"])
        if result["strengths"]:
            st.markdown("**What's working**")
            for s in result["strengths"]:
                st.markdown(f"- ✅ {s}")

    st.divider()
    tab_scores, tab_fix, tab_kw = st.tabs(["📊 Score breakdown", "🛠️ Improvements", "🔑 Keywords"])

    with tab_scores:
        for key, title in SCORE_SECTIONS.items():
            val = result["section_scores"][key]
            st.markdown(f"**{title}** — {val}/100")
            st.progress(val / 100)

    with tab_fix:
        if not result["improvements"]:
            st.success("No major improvements found.")
        order = {"High": 0, "Medium": 1, "Low": 2}
        badge = {"High": "🔴 High", "Medium": "🟠 Medium", "Low": "🟢 Low"}
        for imp in sorted(result["improvements"], key=lambda i: order[i["priority"]]):
            with st.expander(f"{badge[imp['priority']]} · {imp['section']} — {imp['issue'][:90]}"):
                st.markdown(f"**Issue:** {imp['issue']}")
                st.markdown(f"**Fix:** {imp['suggestion']}")
                if imp["example"]:
                    st.markdown("**Example:**")
                    st.markdown(f"> {imp['example']}")

    with tab_kw:
        c1, c2 = st.columns(2)
        with c1:
            st.markdown("**✅ Matched keywords**")
            st.write(", ".join(result["matched_keywords"]) or "—")
        with c2:
            st.markdown("**❌ Missing keywords**" + (" (from job description)" if has_jd else ""))
            st.write(", ".join(result["missing_keywords"]) or "—")

    report = build_report(result)
    st.download_button("⬇️ Download report (.txt)", report, file_name="ats_report.txt", mime="text/plain")


def build_report(result: dict) -> str:
    lines = [f"ATS SCORE: {result['ats_score']}/100", result["verdict"], "", "SECTION SCORES"]
    lines += [f"- {SCORE_SECTIONS[k]}: {v}/100" for k, v in result["section_scores"].items()]
    lines += ["", "STRENGTHS"] + [f"- {s}" for s in result["strengths"]]
    lines += ["", "IMPROVEMENTS"]
    for i, imp in enumerate(result["improvements"], 1):
        lines.append(f"{i}. [{imp['priority']}] {imp['section']}: {imp['issue']}")
        lines.append(f"   Fix: {imp['suggestion']}")
        if imp["example"]:
            lines.append(f"   Example: {imp['example']}")
    lines += ["", "MATCHED KEYWORDS: " + ", ".join(result["matched_keywords"])]
    lines += ["MISSING KEYWORDS: " + ", ".join(result["missing_keywords"])]
    return "\n".join(lines)


# --------------------------------------------------------------------------
# App
# --------------------------------------------------------------------------
def main():
    st.set_page_config(page_title="AI Resume ATS Checker", page_icon="📄", layout="wide")
    st.title("📄 AI Resume ATS Checker")
    st.caption("Upload your resume to get an ATS score and specific improvements, powered by Google Gemini.")

    secret_key = get_secret("GEMINI_API_KEY")
    model = get_secret("GEMINI_MODEL", DEFAULT_MODEL)

    with st.sidebar:
        st.header("⚙️ Settings")
        if secret_key:
            st.success("API key loaded from environment/secrets.")
            api_key = secret_key
        else:
            api_key = st.text_input(
                "Gemini API key",
                type="password",
                help="Get a free key at https://aistudio.google.com/apikey",
            )
        st.text_input("Model", value=model, disabled=True)
        st.markdown("---")
        st.caption("Your resume is sent to the Gemini API for analysis and is not stored by this app.")

    col_a, col_b = st.columns(2)
    with col_a:
        uploaded = st.file_uploader("Upload resume (PDF, DOCX, TXT)", type=["pdf", "docx", "txt"])
    with col_b:
        job_description = st.text_area(
            "Job description (optional, improves keyword matching)",
            height=150,
            placeholder="Paste the job description here...",
        )

    if st.button("🔍 Analyze resume", type="primary", disabled=uploaded is None):
        if not api_key:
            st.error("Please enter your Gemini API key in the sidebar.")
            return
        data = uploaded.getvalue()
        if len(data) > MAX_FILE_MB * 1024 * 1024:
            st.error(f"File is larger than {MAX_FILE_MB} MB.")
            return

        try:
            text = clean_text(extract_text(uploaded.name, data))
        except Exception as exc:  # corrupt / unsupported / encrypted files
            st.error(f"Could not read the file: {exc}")
            return

        if len(text) < MIN_RESUME_CHARS:
            st.error(
                "Very little text could be extracted. If your resume is a scanned image, "
                "export it as a text-based PDF or DOCX — which is also what real ATS systems need."
            )
            return

        resume_hash = hashlib.sha256(text.encode()).hexdigest()
        key_hash = hashlib.sha256(api_key.encode()).hexdigest()
        try:
            with st.spinner("Analyzing your resume with Gemini..."):
                result = cached_analysis(
                    resume_hash, text, job_description.strip(), model, key_hash, api_key
                )
        except json.JSONDecodeError:
            st.error("The AI returned an unreadable response. Please try again.")
            return
        except Exception as exc:
            msg = str(exc)
            if "API key" in msg or "API_KEY" in msg or "401" in msg or "403" in msg:
                st.error("The Gemini API rejected your key. Please check it and try again.")
            elif "429" in msg or "quota" in msg.lower():
                st.error("Rate limit or quota reached. Wait a moment and try again.")
            else:
                st.error(f"Analysis failed: {msg}")
            return

        st.session_state["result"] = result
        st.session_state["has_jd"] = bool(job_description.strip())

    if "result" in st.session_state:
        render_results(st.session_state["result"], st.session_state.get("has_jd", False))


if __name__ == "__main__":
    main()
