# agent_workflow.py
"""
ReACT-style workflow/orchestrator.

- Holds the system prompt (English)
- Parses <CALL>{...}</CALL> blocks from the model's output
- Dispatches to tool functions defined in aidnd_tools.py
- Feeds tool "Observation" back to the model
- Returns the final, user-facing answer

You should implement `call_llm()` with your model provider.
"""
from openai import OpenAI
import json
import re
import os
from typing import Dict, Any, List


from app.engine.catalog import (
    look_monster_table,
    look_table,
    search_table,
    fetch_and_cache,
)

# ================== Plug in your LLM here ==================
def call_llm(messages):
    """
    Minimal OpenAI GPT-4o call compatible with the ReACT workflow.

    Requirements:
      - Install the official client:  pip install openai>=1.0.0
      - Set your key:  export OPENAI_API_KEY="sk-..."
        (or set it in your environment variables on Windows)

    Parameters
    ----------
    messages : list[dict]
        Conversation so far, in the format:
        [{"role": "system", "content": "..."}, {"role": "user", "content": "..."}]

    Returns
    -------
    str : The assistant's text reply.
    """
    client = OpenAI(api_key=os.environ.get("OPENAI_API_KEY"))
    response = client.chat.completions.create(
        model="gpt-4o",
        messages=messages,
        temperature=0.2,
    )
    return response.choices[0].message.content


# ================== System prompt (English) ==================
SYSTEM_PROMPT = (
    "[You are AIDND Assistant]\n"
    "You are a D&D 5e rule assistant. You MUST answer the user's questions in Chinese (Simplified Chinese).\n"
    "You MUST follow this ReACT tool-calling protocol. When you need data from the local catalog or Open5e, you MUST call tools.\n"
    "Do NOT narrate or describe your intentions. Instead, output exactly one tool call block:\n"
    "  <CALL>{\"fn\":\"function_name\",\"args\":{...}}</CALL>\n"
    "After the system executes the tool, it will append a system message beginning with:\n"
    "  Observation: { ... }\n"
    "You may think again, optionally call more tools, and ONLY AFTER calling fetch_and_cache, produce the final user-facing answer.\n"
    "Never include <CALL> in your final answer.\n"
    "Final Answer Requirement: Although you think in tool calls, your final natural language response MUST be in Chinese.\n"
    "\n"
    "Available functions:\n"
    "- look_monster_table(query:str, limit:int=20)\n"
    "- search_table(type:str, name_or_slug:str, prefer_doc:str|None)\n"
    "- fetch_and_cache(type:str, slug:str)\n"
    "\n"
    "Supported resource types (for search_table & fetch_and_cache):\n"
    "  monsters, spells, equipment, backgrounds, classes,\n"
    "  conditions, documents, feats, planes, races,\n"
    "  sections, spelllist\n"
    "\n"
    "==================== EXAMPLES BY CATEGORY ====================\n"
    "\n"
    "### MONSTERS\n"
    "User: Tell me about Zombie.\n"
    "Assistant: <CALL>{\"fn\":\"look_monster_table\",\"args\":{\"query\":\"Zombie\",\"limit\":10}}</CALL>\n"
    "System: Observation: {...}\n"
    "Assistant: <CALL>{\"fn\":\"search_table\",\"args\":{\"type\":\"monsters\",\"name_or_slug\":\"Zombie\",\"prefer_doc\":\"srd-2014\"}}</CALL>\n"
    "System: Observation: {...}\n"
    "Assistant: <CALL>{\"fn\":\"fetch_and_cache\",\"args\":{\"type\":\"monsters\",\"slug\":\"zombie\"}}</CALL>\n"
    "\n"
    "### SPELLS\n"
    "User: Explain the spell Fireball.\n"
    "Assistant: <CALL>{\"fn\":\"search_table\",\"args\":{\"type\":\"spells\",\"name_or_slug\":\"Fireball\",\"prefer_doc\":\"srd-2014\"}}</CALL>\n"
    "System: Observation: {...}\n"
    "Assistant: <CALL>{\"fn\":\"fetch_and_cache\",\"args\":{\"type\":\"spells\",\"slug\":\"fireball\"}}</CALL>\n"
    "\n"
    "### EQUIPMENT\n"
    "User: What is Studded Leather Armor?\n"
    "Assistant: <CALL>{\"fn\":\"search_table\",\"args\":{\"type\":\"equipment\",\"name_or_slug\":\"Studded Leather Armor\",\"prefer_doc\":\"srd-2014\"}}</CALL>\n"
    "System: Observation: {...}\n"
    "Assistant: <CALL>{\"fn\":\"fetch_and_cache\",\"args\":{\"type\":\"equipment\",\"slug\":\"studded-leather-armor\"}}</CALL>\n"
    "\n"
    "### BACKGROUNDS\n"
    "User: Describe the Sage background.\n"
    "Assistant: <CALL>{\"fn\":\"search_table\",\"args\":{\"type\":\"backgrounds\",\"name_or_slug\":\"Sage\",\"prefer_doc\":\"srd-2014\"}}</CALL>\n"
    "\n"
    "### CONDITIONS\n"
    "User: What is the Grappled condition?\n"
    "Assistant: <CALL>{\"fn\":\"search_table\",\"args\":{\"type\":\"conditions\",\"name_or_slug\":\"Grappled\",\"prefer_doc\":null}}</CALL>\n"
    "\n"
    "### RACES\n"
    "User: Tell me about Dwarf.\n"
    "Assistant: <CALL>{\"fn\":\"search_table\",\"args\":{\"type\":\"races\",\"name_or_slug\":\"Dwarf\",\"prefer_doc\":\"srd-2014\"}}</CALL>\n"
    "\n"
    "### CLASSES\n"
    "User: Explain the Wizard class.\n"
    "Assistant: <CALL>{\"fn\":\"search_table\",\"args\":{\"type\":\"classes\",\"name_or_slug\":\"Wizard\"}}</CALL>\n"
    "\n"
    "### FEATS\n"
    "User: Show me the Sharpshooter feat.\n"
    "Assistant: <CALL>{\"fn\":\"search_table\",\"args\":{\"type\":\"feats\",\"name_or_slug\":\"Sharpshooter\"}}</CALL>\n"
    "\n"
    "### PLANES\n"
    "User: What is the Astral Plane?\n"
    "Assistant: <CALL>{\"fn\":\"search_table\",\"args\":{\"type\":\"planes\",\"name_or_slug\":\"Astral Plane\"}}</CALL>\n"
    "\n"
    "### SECTIONS (RULEBOOK CHAPTERS)\n"
    "User: Show the rules for Two-Weapon Fighting.\n"
    "Assistant: <CALL>{\"fn\":\"search_table\",\"args\":{\"type\":\"sections\",\"name_or_slug\":\"Two-Weapon Fighting\"}}</CALL>\n"
    "\n"
    "==============================================================\n"
    "Use these examples as templates. ALWAYS follow this exact format.\n"
)



# ================== CALL parsing & dispatch ==================
CALL_RE = re.compile(r"<CALL>([\s\S]+?)</CALL>", re.IGNORECASE)

def _maybe_execute_tool(text: str) -> Dict[str, Any] | None:
    """
    Detect a single <CALL>{...}</CALL> in the assistant text.
    If found and JSON is valid, dispatch to the tool.
    If the JSON is invalid, treat it as NO tool call (let the outer loop handle it).
    """
    m = CALL_RE.search(text or "")
    if not m:
        return None

    try:
        payload = json.loads(m.group(1))
        fn = payload["fn"]
        args = payload.get("args", {})
    except Exception as e:
        # 解析失败：写日志，但告诉外层“没有有效工具调用”
        import logging
        logging.warning(f"CALL parse error: {e} | raw={m.group(1)}")
        return None

    try:
        if fn == "look_monster_table":
            result = look_monster_table(
                query=args.get("query", ""),
                limit=int(args.get("limit", 20)),
            )
        elif fn == "look_table":
            result = look_table(
                res_type=args.get("type", "monsters"),
                query=args.get("query", ""),
                limit=int(args.get("limit", 20)),
            )
        elif fn == "search_table":
            result = search_table(
                res_type=args.get("type", "monsters"),
                name_or_slug=args.get("name_or_slug", ""),
                prefer_doc=args.get("prefer_doc"),
            )
        elif fn == "fetch_and_cache":
            result = fetch_and_cache(
                res_type=args.get("type", "monsters"),
                slug=args.get("slug", ""),
            )
        else:
            return {"fn": fn, "args": args, "error": f"unknown tool: {fn}"}
        return {"fn": fn, "args": args, "result": result}
    except Exception as e:
        return {"fn": fn, "args": args, "error": str(e)}



# ================== Public entry: one full turn ==================

import logging
from datetime import datetime
import os

# Setup logger once
log_dir = "logs"
os.makedirs(log_dir, exist_ok=True)
log_filename = os.path.join(log_dir, f"session_{datetime.now().strftime('%Y%m%d_%H%M%S')}.log")

logging.basicConfig(
    filename=log_filename,
    filemode="w",
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
)

def answer_query(user_query: str, max_tool_steps: int = 6) -> str:
    """
    ReACT main loop with logging and enforced tool-calling.

    - 日志仍然写入 logs/session_*.log
    - 在成功调用过一次 fetch_and_cache 之前，如果模型没有输出 <CALL>，
      会追加一条 system 提示，强制它下次必须输出工具调用，而不是直接回答。
    """
    msgs = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": user_query},
    ]

    fetched_once = False  # 是否已经成功 fetch_and_cache 过一次

    logging.info("=" * 80)
    logging.info(f"[USER QUERY] {user_query}")
    logging.info("=" * 80)

    for step in range(max_tool_steps):
        logging.info(f"\n[STEP {step + 1}] Sending messages to model:")
        for m in msgs[-3:]:
            truncated = m["content"][:1000] + ("..." if len(m["content"]) > 1000 else "")
            logging.info(f"  {m['role'].upper()}: {truncated}")

        assistant_text = call_llm(msgs)
        logging.info(f"[MODEL OUTPUT STEP {step + 1}] ----------------------------")
        logging.info(assistant_text)
        logging.info("------------------------------------------------------------")

        msgs.append({"role": "assistant", "content": assistant_text})

        call = _maybe_execute_tool(assistant_text)
        if not call:
            # 如果还没从 Open5e 拉过详情，就强制它再试一次工具调用
            if not fetched_once:
                logging.info("[NO TOOL CALL] But no fetch yet -> adding reminder and retrying.")
                msgs.append({
                    "role": "system",
                    "content": (
                        "Reminder: You MUST call a tool next. "
                        "Output exactly one block like "
                        "<CALL>{\"fn\":\"...\",\"args\":{...}}</CALL>. "
                        "Do NOT give a final answer yet."
                    ),
                })
                continue

            # 已经有过 fetch_and_cache，则把这次当做最终回答
            logging.info("[NO TOOL CALL DETECTED] Returning final answer.\n")
            logging.info("=" * 80)
            logging.info(f"[FINAL ANSWER]\n{assistant_text}")
            logging.info("=" * 80)
            return assistant_text

        # 有工具调用：执行并记录 Observation
        logging.info(f"[TOOL CALL DETECTED] {call.get('fn')} with args = {call.get('args', {})}")

        if call.get("fn") == "fetch_and_cache" and "result" in call and not call.get("error"):
            fetched_once = True
            # 关键：告诉模型“现在已经有完整 JSON，下一条要回答用户，不能再 CALL”
            msgs.append({
                "role": "system",
                "content": (
                    "You have just received the full JSON data from fetch_and_cache. "
                    "Now you MUST answer the user's question in natural language, "
                    "using that data. Do NOT call any tools again, and do NOT output "
                    "any <CALL> blocks in your next message."
                ),
            })

        observation_json = json.dumps(call, ensure_ascii=False, indent=2)
        logging.info(f"[OBSERVATION]\n{observation_json}")
        msgs.append({"role": "system", "content": f"Observation: {observation_json}"})


    logging.warning("[TOOL CALL LIMIT REACHED] The model did not finish within the allowed steps.")
    return "Tool call limit reached. Please provide a final answer based on the observations so far."

