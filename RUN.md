# SA Runner — Windows Setup & Launch Guide

## Prerequisites

**Microsoft Word is required** (any 2016+ version). SA Runner uses Word to convert the rendered report from .docx to .pdf at the end of each job. Word must be installed but does not need to be running — the worker drives it silently via PowerShell COM. You do not need to install Python, Node.js, or any other software beforehand. `Start SA Runner.bat` self-bootstraps the rest of the runtime (Python 3.11, Node 20 LTS) on first launch, downloading and installing everything into your user profile automatically.

---

## 1. Install

Extract the release ZIP to any folder on your machine. Avoid paths that contain special characters. Example:

```
C:\Users\YourName\SA Runner\
```

The extracted folder should contain `Start SA Runner.bat`, `Stop SA Runner.bat`, `sentiment-analysis.skill`, and the `app\`, `frontend\`, and `scripts\` directories.

---

## 2. Configure

Create a `.env` file in the same folder as `Start SA Runner.bat` and set your App Password.

**PowerShell:**
```powershell
Set-Content -Path ".env" -Value "APP_PASSWORD=your-chosen-password"
```

**Command Prompt (cmd):**
```cmd
echo APP_PASSWORD=your-chosen-password> .env
```

Replace `your-chosen-password` with a password of your choice. This is the password you will use to sign in to SA Runner.

> **Tip:** If you skip this step, `Start SA Runner.bat` will prompt you to enter a password interactively on the first launch and create `.env` for you.

---

## 3. Launch

Double-click `Start SA Runner.bat`, or run it from PowerShell or cmd:

**PowerShell:**
```powershell
& ".\Start SA Runner.bat"
```

**Command Prompt (cmd):**
```cmd
"Start SA Runner.bat"
```

On the **first launch** the bootstrap script runs automatically. It downloads and installs the Bundled Runtime components — this may take several minutes depending on your internet connection. Subsequent launches skip any components already installed and start in seconds.

When the server is ready, your default browser opens automatically to `http://127.0.0.1:8770/`.

---

## 4. Verify

1. **Sign-in page loads** — the browser should show the SA Runner sign-in form at `http://127.0.0.1:8770/`.
2. **Sign in** — enter the App Password you set in `.env`.
3. **New-job form is visible** — after signing in, the dashboard displays the new-job submission form.
4. **Settings shows all four providers** — navigate to **Settings** (top-right link) and confirm that all four API key fields are present and configurable:
   - **Anthropic**
   - **Perplexity**
   - **xAI**
   - **FMP** (Financial Modeling Prep)

---

## 5. Enter API Keys

SA Runner requires API keys for four external providers. After signing in, go to **Settings** and paste in each key:

| Provider | Where to obtain |
|---|---|
| **Anthropic** | [console.anthropic.com](https://console.anthropic.com) → API Keys |
| **Perplexity** | [perplexity.ai/settings/api](https://www.perplexity.ai/settings/api) |
| **xAI** | [x.ai/api](https://x.ai/api) |
| **FMP** (Financial Modeling Prep) | [financialmodelingprep.com/developer](https://financialmodelingprep.com/developer/docs) |

Keys are stored encrypted on your machine and are never written to disk in plaintext.

---

## Stopping SA Runner

Double-click `Stop SA Runner.bat`, or close the **SA Runner — Server** console window that opened when you launched.

---

## Troubleshooting

| Symptom | Fix |
|---|---|
| Bootstrap fails with a SHA256 mismatch | Delete `%LOCALAPPDATA%\sa-runner\downloads\` and re-run `Start SA Runner.bat` to re-download. |
| Port 8770 already in use | Run `Stop SA Runner.bat` to stop a previous instance, or restart your machine. |
| Browser does not open automatically | Navigate to `http://127.0.0.1:8770/` manually. |
| Server window shows a Python import error | Ensure you extracted the full ZIP (not just the `.bat` file). The `app\` directory must be present. |
