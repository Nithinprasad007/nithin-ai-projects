# Smart Browser MCP Agent 🌐🤖

An autonomous, multi-turn AI browser automation system built using the **Model Context Protocol (MCP)**, **Puppeteer/Playwright**, and **LangChain**.

Unlike standard browser automation scripts that terminate after a single instruction, this agent keeps an active Chromium session persistent across consecutive commands, dynamically inspects live DOM states to eliminate selector hallucinations, and supports fully private local execution using **Ollama** alongside cloud LLMs.

---

## 🚀 Key Features

- **Multi-Turn Persistent Browser:** Chromium stays open and interactive across multiple sequential prompts until explicitly closed.
- **Dual-Inference Engine:** Run offline with privacy using **Ollama** (`qwen2.5:7b`, `llama3.1:8b`) or cloud fallback with **OpenAI** (`gpt-4o`).
- **Active DOM Grounding:** Injects an active JavaScript DOM observer after every navigation or interaction, providing the LLM with verified CSS selectors, live URLs, and element labels.
- **Real-Time Visual Sync:** Forces Chrome tab alignment (`window.location.assign` and viewport focusing) so you can visually watch the agent interact in real time.
- **Queue-Driven Session Worker:** Decouples Streamlit event loops from the persistent MCP stdio process, preventing task-group cancellation errors.

---

## 📁 Project Structure

```text
browser-mcp-agent/
├── .env.example          # Sample environment configurations
├── requirements.txt      # Python dependencies
├── llm_provider.py       # Model dispatcher (Ollama & OpenAI)
├── agent.py              # Core MCP loop, DOM inspector & action execution
├── app.py                # Streamlit UI with persistent background queue
└── README.md             # Project documentation
```

---

## 🛠️ Prerequisites

- **Python 3.10+** (Tested on Python 3.11 - 3.14)
- **Node.js (v18+)** and npm installed with system PATH access
- **Ollama** installed and running (for local offline mode):
  ```bash
  ollama pull qwen2.5:7b
  ```

---

## ⚙️ Installation & Setup

### 1. Set Up Virtual Environment

- **Windows (PowerShell):**
  ```powershell
  python -m venv venv
  Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
  .\venv\Scripts\Activate.ps1
  ```
- **Linux / macOS:**
  ```bash
  python3 -m venv venv
  source venv/bin/activate
  ```

### 2. Install Dependencies

```powershell
python -m pip install --upgrade pip
pip install -r requirements.txt
playwright install
npm install -g @modelcontextprotocol/server-puppeteer
```

### 3. Configure Environment Variables (Optional)

If using OpenAI models instead of local Ollama, create a `.env` file:
```env
OPENAI_API_KEY=your_openai_api_key_here
OLLAMA_HOST=http://localhost:11434
```

---

## 🖥️ Running the Application

### 1. Start Ollama (Local Mode)
```powershell
ollama run qwen2.5:7b
```

### 2. Launch Streamlit
```powershell
python -m streamlit run app.py
```
Open your browser at `http://localhost:8501`.

---

## 💡 Interactive Workflow Example

Send instructions sequentially into the chat input bar:

1. **Command 1:** `Go to https://books.toscrape.com`
2. **Command 2:** `Click on the first book title`
3. **Command 3:** `What is the price and availability status?`
4. When finished, click **Close / Reset Browser** in the sidebar to terminate Chromium cleanly.

---

## 🛡️ License

Distributed under the MIT License.
