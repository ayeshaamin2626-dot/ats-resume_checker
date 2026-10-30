# ats-resume_checker# 📄 AI Resume ATS Checker

A Streamlit app that scores a resume for Applicant Tracking System (ATS) compatibility and gives specific, prioritised improvements, powered by Google's Gemini Flash model.

## Features

- Upload a resume as **PDF, DOCX or TXT**
- Optional **job description** field for targeted keyword matching
- **ATS score (0-100)** with a breakdown: keywords, formatting, experience, education, summary, grammar
- **Matched and missing keywords**
- **Prioritised improvements** (High / Medium / Low) with example rewrites
- Downloadable text report

## Project structure

```
app.py             # Streamlit app
requirements.txt   # Python dependencies
README.md          # This file
```

## Run locally

1. Get a free Gemini API key at https://aistudio.google.com/apikey
2. Install and run:

```bash
python -m venv venv
source venv/bin/activate        # Windows: venv\Scripts\activate
pip install -r requirements.txt

export GEMINI_API_KEY="your-key"   # Windows PowerShell: $env:GEMINI_API_KEY="your-key"
streamlit run app.py
```

If you don't set the environment variable, the app shows a password box in the sidebar where you can paste the key.

## Configuration

| Name | Purpose | Default |
|------|---------|---------|
| `GEMINI_API_KEY` | Your Gemini API key (env var or Streamlit secret) | none |
| `GEMINI_MODEL` | Gemini model ID to use | `gemini-3.8-flash` |

For local Streamlit secrets, create `.streamlit/secrets.toml` (do **not** commit it):

```toml
GEMINI_API_KEY = "your-key"
```

## Push to GitHub (using the website, no command line)

1. Sign in at https://github.com and click **+ → New repository**.
2. Name it (e.g. `ats-resume-checker`), choose **Public**, and click **Create repository**.
3. On the new repo page click **uploading an existing file**.
4. Drag in `app.py`, `requirements.txt` and `README.md`, then click **Commit changes**.
5. Never upload your API key or a `secrets.toml` file.

## Deploy on Streamlit Community Cloud

1. Go to https://share.streamlit.io and sign in with GitHub.
2. Click **Create app → Deploy a public app from GitHub**.
3. Choose your repository, branch `main`, and main file path `app.py`.
4. Open **Advanced settings → Secrets** and paste:

   ```toml
   GEMINI_API_KEY = "your-key"
   ```

5. Click **Deploy**. After a minute or two you get a public `*.streamlit.app` URL.

To update the app later, edit files on GitHub (pencil icon → Commit); Streamlit redeploys automatically.

## Notes

- Scanned/image-only PDFs cannot be read; use a text-based PDF or DOCX.
- The score is an AI estimate, not the output of a real ATS. Use it as guidance.
- Resume text is sent to the Gemini API for analysis and is not stored by this app.
