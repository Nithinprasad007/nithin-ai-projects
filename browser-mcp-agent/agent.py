import os
import sys
import shutil
import asyncio
import json
import re
import traceback
from typing import List, Dict, Any, Type
from pydantic import create_model, Field
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from langchain_core.tools import StructuredTool
from langchain_core.messages import HumanMessage, SystemMessage, ToolMessage
from llm_provider import get_chat_model

SYSTEM_PROMPT = """You are an autonomous browser automation agent.
You navigate and click elements using the provided tools.
- When navigating, provide the exact destination URL.
- When clicking, select the exact CSS selector from the updated page state.
- When finished, summarize your answer clearly and stop calling tools.
"""

# Enhanced script that forces the visible window to redirect and focuses it
DOM_INSPECTOR_JS = """(() => {
    try { window.focus(); } catch(e) {}
    
    const title = document.title || "";
    const url = window.location.href || "";
    const bodyText = document.body ? document.body.innerText.slice(0, 1200) : "";
    
    const elements = Array.from(document.querySelectorAll('a, button, input, select, textarea, [role="button"]'))
        .filter(el => {
            const rect = el.getBoundingClientRect();
            return rect.width > 0 && rect.height > 0 && window.getComputedStyle(el).visibility !== 'hidden';
        })
        .slice(0, 35)
        .map((el, i) => {
            const tag = el.tagName.toLowerCase();
            const text = (el.innerText || el.value || el.placeholder || el.getAttribute('aria-label') || '').trim().replace(/\s+/g, ' ').slice(0, 50);
            let selector = '';
            if (el.id) {
                selector = `#${el.id}`;
            } else if (el.name) {
                selector = `${tag}[name="${el.name}"]`;
            } else if (tag === 'a' && el.getAttribute('href')) {
                const href = el.getAttribute('href');
                selector = href.startsWith('http') ? `a[href="${href}"]` : `a[href*="${href.slice(0, 25)}"]`;
            } else {
                selector = tag;
            }
            return `[${i + 1}] <${tag}> "${text}" -> selector: '${selector}'`;
        });

    return JSON.stringify({
        url: url,
        title: title,
        visible_elements: elements,
        page_preview: bodyText.replace(/\n\s*\n/g, '\n')
    }, null, 2);
})()"""

def extract_first_url(data: Any) -> str | None:
    if isinstance(data, str):
        match = re.search(r'https?://[^\s"\'<>]+', data)
        if match:
            return match.group(0)
    elif isinstance(data, dict):
        for k, v in data.items():
            if k.lower() in ["url", "link", "href", "target", "uri"] and isinstance(v, str) and v.startswith("http"):
                return v
            res = extract_first_url(v)
            if res:
                return res
    elif isinstance(data, (list, tuple)):
        for item in data:
            res = extract_first_url(item)
            if res:
                return res
    return None

def create_pydantic_model_from_schema(schema: dict, model_name: str) -> Type:
    properties = schema.get("properties", {})
    required = set(schema.get("required", []))
    fields = {}

    type_mapping = {
        "string": str,
        "integer": int,
        "number": float,
        "boolean": bool,
        "array": list,
        "object": dict
    }

    for prop_name, prop_data in properties.items():
        json_type = prop_data.get("type", "string")
        py_type = type_mapping.get(json_type, Any)
        default = ... if prop_name in required else prop_data.get("default", None)
        desc = prop_data.get("description", "")
        fields[prop_name] = (py_type, Field(default=default, description=desc))

    if not fields:
        return create_model(model_name)
    return create_model(model_name, **fields)

class BrowserMCPAgent:
    def __init__(self, provider: str = "ollama", model_name: str = "qwen2.5:7b", base_url: str = "http://localhost:11434"):
        self.provider = provider
        self.model_name = model_name
        self.base_url = base_url
        self.llm = get_chat_model(provider=provider, model_name=model_name, base_url=base_url)
        
        npx_target = "npx.cmd" if sys.platform == "win32" else "npx"
        resolved_cmd = shutil.which(npx_target) or shutil.which("npx")
        if not resolved_cmd:
            raise FileNotFoundError("Could not find 'npx' or 'npx.cmd' in PATH.")

        # Force puppeteer to launch headfully with explicit window management
        env_vars = os.environ.copy()
        launch_opts = {
            "headless": False,
            "defaultViewport": None,
            "args": ["--start-maximized", "--no-default-browser-check"]
        }
        env_vars["PUPPETEER_LAUNCH_OPTIONS"] = json.dumps(launch_opts)
        env_vars["ALLOW_DANGEROUS"] = "true"

        self.server_params = StdioServerParameters(
            command=resolved_cmd,
            args=["-y", "@modelcontextprotocol/server-puppeteer"],
            env=env_vars
        )
        
        self.queue: asyncio.Queue = asyncio.Queue()
        self.worker_task: asyncio.Task | None = None
        self.is_running = False
        self.messages: List[Any] = [SystemMessage(content=SYSTEM_PROMPT)]

    async def start(self):
        if self.worker_task and not self.worker_task.done():
            return
        self.is_running = True
        loop = asyncio.get_running_loop()
        ready_event = asyncio.Event()
        self.worker_task = loop.create_task(self._session_loop(ready_event))
        await ready_event.wait()

    async def stop(self):
        if not self.is_running or not self.worker_task:
            return
        done_future = asyncio.get_running_loop().create_future()
        await self.queue.put(("STOP", None, done_future))
        await done_future
        self.is_running = False
        if self.worker_task:
            await self.worker_task
            self.worker_task = None

    async def run_task(self, task_prompt: str, max_iterations: int = 12):
        if not self.is_running or not self.worker_task:
            await self.start()
        
        loop = asyncio.get_running_loop()
        future = loop.create_future()
        await self.queue.put(("EXEC", (task_prompt, max_iterations), future))
        return await future

    async def _session_loop(self, ready_event: asyncio.Event):
        try:
            async with stdio_client(self.server_params) as (read, write):
                async with ClientSession(read, write) as session:
                    await session.initialize()
                    mcp_tools_list = await session.list_tools()
                    lc_tools = self._convert_mcp_to_langchain_tools(session, mcp_tools_list)
                    tool_dict = {tool.name: tool for tool in lc_tools}
                    model_with_tools = self.llm.bind_tools(lc_tools)

                    ready_event.set()

                    while True:
                        cmd, payload, future = await self.queue.get()
                        
                        if cmd == "STOP":
                            future.set_result(True)
                            self.queue.task_done()
                            break

                        if cmd == "EXEC":
                            task_prompt, max_iterations = payload
                            try:
                                result, traces = await self._execute_agent_loop(
                                    session, model_with_tools, tool_dict, task_prompt, max_iterations
                                )
                                future.set_result((result, traces))
                            except Exception as e:
                                future.set_exception(e)
                            finally:
                                self.queue.task_done()
        except Exception as e:
            if not ready_event.is_set():
                ready_event.set()
            raise e

    async def _get_grounded_page_state(self, session: ClientSession) -> str:
        try:
            eval_res = await session.call_tool("puppeteer_evaluate", arguments={"script": DOM_INSPECTOR_JS})
            raw_content = eval_res.content
            if isinstance(raw_content, list) and len(raw_content) > 0 and hasattr(raw_content[0], "text"):
                parsed = json.loads(raw_content[0].text)
            elif isinstance(raw_content, str):
                parsed = json.loads(raw_content)
            else:
                return "Could not retrieve DOM state."

            formatted = (
                f"\n=== UPDATED PAGE STATE ===\n"
                f"URL: {parsed.get('url')}\n"
                f"Title: {parsed.get('title')}\n"
                f"--- Interactive Elements (Use these exact selectors) ---\n"
                + "\n".join(parsed.get("visible_elements", [])) +
                f"\n--- Page Text Preview ---\n{parsed.get('page_preview', '')[:600]}\n"
                f"===========================\n"
            )
            return formatted
        except Exception:
            return "\n[Action executed, page loaded]\n"

    def _convert_mcp_to_langchain_tools(self, session: ClientSession, mcp_tools) -> List[StructuredTool]:
        tools = []
        for tool in mcp_tools.tools:
            schema_dict = tool.inputSchema if hasattr(tool, "inputSchema") and tool.inputSchema else {}
            pydantic_schema = create_pydantic_model_from_schema(schema_dict, f"{tool.name}Schema")

            def make_tool_func(tool_name: str):
                async def run_tool(*args, **kwargs):
                    final_args = {}
                    if args and isinstance(args[0], dict):
                        final_args.update(args[0])
                    final_args.update(kwargs)

                    target_url = None
                    # Normalizing navigation URL
                    if "navigate" in tool_name.lower():
                        detected_url = extract_first_url(final_args)
                        if not detected_url:
                            raw_candidate = final_args.get("url") or final_args.get("link") or ""
                            if isinstance(raw_candidate, str) and raw_candidate.strip():
                                detected_url = f"https://{raw_candidate.strip().lstrip('/')}"
                            else:
                                detected_url = "https://books.toscrape.com"
                        final_args = {"url": detected_url}
                        target_url = detected_url

                    # Normalizing click selector
                    if "click" in tool_name.lower():
                        if "selector" not in final_args:
                            for alt in ["element", "target", "query"]:
                                if alt in final_args:
                                    final_args["selector"] = final_args.pop(alt)
                                    break

                    # If this is a navigation action, directly force the open tab to navigate using window.location.assign
                    if target_url:
                        try:
                            # 1. Force the active visible window tab to navigate
                            force_nav_script = f"(() => {{ window.location.assign('{target_url}'); }})()"
                            await session.call_tool("puppeteer_evaluate", arguments={"script": force_nav_script})
                            await asyncio.sleep(2.0)
                        except Exception:
                            pass

                    # 2. Call standard MCP tool
                    raw_result = await session.call_tool(tool_name, arguments=final_args)
                    
                    # 3. Allow page transition to settle and inspect DOM
                    if any(action in tool_name.lower() for action in ["navigate", "click", "fill", "hover"]):
                        await asyncio.sleep(1.5)
                        grounded_state = await self._get_grounded_page_state(session)
                        return f"Action '{tool_name}' succeeded.\n{grounded_state}"

                    return raw_result.content
                return run_tool

            tools.append(
                StructuredTool.from_function(
                    coroutine=make_tool_func(tool.name),
                    name=tool.name,
                    description=tool.description or f"Tool: {tool.name}",
                    args_schema=pydantic_schema
                )
            )
        return tools

    async def _execute_agent_loop(self, session, model_with_tools, tool_dict, task_prompt: str, max_iterations: int):
        self.messages.append(HumanMessage(content=task_prompt))
        logs = []

        for step in range(max_iterations):
            response = await model_with_tools.ainvoke(self.messages)
            self.messages.append(response)

            if not response.tool_calls:
                logs.append({"step": step + 1, "thought": response.content, "action": "DONE"})
                return response.content, logs

            for tool_call in response.tool_calls:
                name = tool_call["name"]
                raw_args = tool_call.get("args", {})
                tool_id = tool_call.get("id", f"call_{step}")
                
                if isinstance(raw_args, str):
                    try:
                        raw_args = json.loads(raw_args)
                    except Exception:
                        raw_args = {"selector": raw_args}

                logs.append({"step": step + 1, "thought": response.content, "action": f"{name}({raw_args})"})
                
                if name in tool_dict:
                    try:
                        tool_result = await tool_dict[name].ainvoke(raw_args)
                    except Exception as e:
                        tool_result = f"Tool Execution Failed: {str(e)}. Pick another selector from state."
                else:
                    tool_result = f"Error: Tool {name} not found."

                self.messages.append(ToolMessage(content=str(tool_result), tool_call_id=tool_id))

        return "Reached maximum execution steps without completion.", logs
