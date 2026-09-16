import asyncio
import threading
import streamlit as st
import ollama
from agent import BrowserMCPAgent

st.set_page_config(page_title="Persistent Browser MCP Agent", layout="wide")

st.title("🌐 Persistent Browser MCP Agent")
st.caption("Live, interactive browser session that stays open across commands")

class AsyncWorker:
    def __init__(self):
        self.loop = asyncio.new_event_loop()
        self.thread = threading.Thread(target=self._run_loop, daemon=True)
        self.thread.start()

    def _run_loop(self):
        asyncio.set_event_loop(self.loop)
        self.loop.run_forever()

    def run(self, coro):
        future = asyncio.run_coroutine_threadsafe(coro, self.loop)
        return future.result()

# Maintain singleton background event loop
if "worker" not in st.session_state:
    st.session_state.worker = AsyncWorker()

if "agent" not in st.session_state:
    st.session_state.agent = None

if "chat_history" not in st.session_state:
    st.session_state.chat_history = []

# Sidebar Controls
st.sidebar.header("Agent Settings")
provider = st.sidebar.selectbox("LLM Provider", ["Ollama (Local)", "OpenAI"])

if provider == "Ollama (Local)":
    ollama_url = st.sidebar.text_input("Ollama Host", "http://localhost:11434")
    try:
        client = ollama.Client(host=ollama_url)
        available_models = [m['model'] for m in client.list()['models']]
        if not available_models:
            available_models = ["qwen2.5:7b", "llama3.1:8b", "mistral:latest"]
    except Exception:
        available_models = ["qwen2.5:7b", "llama3.1:8b", "mistral:latest"]
    selected_model = st.sidebar.selectbox("Model", available_models)
else:
    ollama_url = ""
    selected_model = st.sidebar.selectbox("Model", ["gpt-4o", "gpt-4o-mini"])
    api_key = st.sidebar.text_input("OpenAI API Key", type="password")

# Browser Status & Reset
st.sidebar.markdown("---")
st.sidebar.subheader("Session Control")
is_active = st.session_state.agent is not None and st.session_state.agent.is_running
st.sidebar.write(f"Status: {'🟢 Browser Running' if is_active else '⚪ Idle / Closed'}")

if st.sidebar.button("Close / Reset Browser", type="secondary"):
    if st.session_state.agent:
        st.session_state.worker.run(st.session_state.agent.stop())
        st.session_state.agent = None
        st.session_state.chat_history = []
        st.sidebar.success("Browser closed cleanly.")
        st.rerun()

# Display Conversation History
for chat in st.session_state.chat_history:
    with st.chat_message("user"):
        st.write(chat["query"])
    with st.chat_message("assistant"):
        st.write(chat["response"])
        if chat.get("traces"):
            with st.expander("Show Steps Executed"):
                for entry in chat["traces"]:
                    st.write(f"**Step {entry['step']}** - Action: `{entry['action']}`")

# Continuous User Input Bar
user_input = st.chat_input("Enter browser command (e.g. 'Go to https://books.toscrape.com')...")

if user_input:
    with st.chat_message("user"):
        st.write(user_input)

    prov_key = "ollama" if "Ollama" in provider else "openai"
    if st.session_state.agent is None:
        st.session_state.agent = BrowserMCPAgent(
            provider=prov_key,
            model_name=selected_model,
            base_url=ollama_url or "http://localhost:11434"
        )
        with st.spinner("Spawning persistent browser..."):
            st.session_state.worker.run(st.session_state.agent.start())

    with st.chat_message("assistant"):
        with st.spinner("Executing on active browser..."):
            try:
                response_text, traces = st.session_state.worker.run(
                    st.session_state.agent.run_task(user_input)
                )
                st.write(response_text)
                
                with st.expander("Show Steps Executed"):
                    for entry in traces:
                        st.write(f"**Step {entry['step']}** - Action: `{entry['action']}`")

                st.session_state.chat_history.append({
                    "query": user_input,
                    "response": response_text,
                    "traces": traces
                })
            except Exception as e:
                st.error(f"Error during execution: {e}")
