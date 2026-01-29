import json
import os
from typing import Literal

from langchain_core.tools import tool
from langchain_core.messages import SystemMessage, HumanMessage, ToolMessage
from langchain_openai import ChatOpenAI
from langgraph.graph import StateGraph, END
from langgraph.prebuilt import ToolNode

from app.engine.state import NarrativeAgentState
from app.engine.session import session_manager
from app.engine.combat import roll_dice
from app.config import STORIES_DIR
from app.schemas import DMResponse
from app.engine.i18n import get_text

# --- 1. Tools Definition ---

# ABILITY_CHECK_DEF remains same...
ABILITY_CHECK_DEF = {
    "name": "ability_check",
    "description": (
        "Perform a NON-COMBAT ability check for the player.\n"
        "You MUST choose exactly one ability from: strength, dexterity, constitution, "
        "intelligence, wisdom, charisma.\n"
        "The game engine will look up the character's actual ability score, compute the "
        "modifier, roll 1d20+modifier, and determine success or failure against the DC.\n"
        "Use this ONLY for things like Perception, Stealth, Persuasion, etc. "
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "ability": {
                "type": "string",
                "enum": ["strength", "dexterity", "constitution", "intelligence", "wisdom", "charisma"]
            },
            "dc": {"type": "integer"},
            "reason": {"type": "string"}
        },
        "required": ["ability", "dc", "reason"]
    }
}

# --- 2. Node Functions ---

def load_context(state: NarrativeAgentState):
    """
    Load session, player, story node, and build System Prompt + Context.
    """
    session_id = state["session_id"]
    session = session_manager.load_session(session_id)
    player = session.players[0]
    lang = getattr(session, "language", "en")
    
    story_path = STORIES_DIR / session.story_id / "story.json"
    with open(story_path, "r", encoding="utf-8") as f:
        story_data = json.load(f)
    current_node = story_data["nodes"].get(session.current_node_id)
    
    # Pacing Logic - READ ONLY (Increment handled by Wrapper)
    # session.current_node_turns has already been incremented by the wrapper before calling this graph.
    min_turns = current_node.get("min_turns", 2)
    
    # Build Context String (Reuse logic from ai_dm.py but cleaner)
    options = current_node.get("options", [])
    interactions = current_node.get("interactions", [])
    edges = current_node.get("edges", [])
    
    edge_ids = [e.get("to") for e in edges if e.get("to") in story_data["nodes"]]
    edges_text = "\n".join(f"- {eid}" for eid in edge_ids) if edge_ids else get_text(lang, "dm_context", "edges_default")
    options_text = "\n".join(f"- {opt}" for opt in options) if options else get_text(lang, "dm_context", "options_default")
    
    interactions_text = get_text(lang, "dm_context", "interactions_default")
    if interactions:
        lines = []
        for inter in interactions:
            lines.append(f"- Trigger: {inter.get('trigger')}\n  Mechanic: {inter.get('mechanic')}\n  Success: {inter.get('success')}\n  Failure: {inter.get('failure')}")
        interactions_text = "\n".join(lines)
        
    pacing_instruction = get_text(lang, "dm_context", "pacing_go")
    if session.current_node_turns < min_turns:
        pacing_instruction = get_text(lang, "dm_context", "pacing_wait").format(turns=session.current_node_turns, min_turns=min_turns)

    context = f"""
    --- PLAYER ---
    Name: {player.name} | HP: {player.current_hp}

    --- CURRENT SCENE ---
    Title: {current_node.get('title')} ({current_node.get('type')})
    Description: {current_node.get('read_aloud')}
    GM Secrets: {current_node.get('gm_guidance')}

    --- EXITS ---
    {json.dumps(edges, indent=2, ensure_ascii=False)}

    --- PLAYER OPTIONS ---
    {options_text}

    --- INTERACTIONS ---
    {interactions_text}

    --- POSSIBLE NEXT NODE IDS ---
    {edges_text}

    --- INSTRUCTIONS ---
    {pacing_instruction}
    
    Player says: "{state['player_input']}"
    """
    
    system_prompt = get_text(lang, "system_dm")
    
    # Inject sanitized history
    history_msgs = []
    for msg in session.chat_history[-6:]:
        if msg["role"] == "data":
            history_msgs.append(SystemMessage(content=f"[Previous Log]: {msg['content']}"))
        elif msg["role"] == "user":
            history_msgs.append(HumanMessage(content=msg["content"]))
        elif msg["role"] == "assistant":
            # We treat assistant history as AI message
            # But LangChain expects AIMessage. We can just use SystemMessage for simplicity in context or AIMessage
            from langchain_core.messages import AIMessage
            history_msgs.append(AIMessage(content=msg["content"]))

    return {
        "system_prompt": system_prompt,
        "player_name": player.name,
        "current_node": current_node,
        "story_context_text": context,
        "messages": history_msgs + [HumanMessage(content=context)],
        "mechanics_logs": []
    }

def call_model(state: NarrativeAgentState):
    """
    Call the LLM with tools bound.
    """
    if os.getenv("OPENAI_API_KEY"):
        llm = ChatOpenAI(model="gpt-4o", temperature=0.7)
    elif os.getenv("DEEPSEEK_API_KEY"):
        llm = ChatOpenAI(
            model="deepseek-chat", 
            api_key=os.getenv("DEEPSEEK_API_KEY"), 
            base_url="https://api.deepseek.com",
            temperature=0.7
        )
    else:
        raise ValueError("No API Key")
        
    # Bind raw JSON schema for ability_check
    llm_with_tools = llm.bind_tools([ABILITY_CHECK_DEF])
    
    # Construct messages: System + History + User Context
    all_msgs = [SystemMessage(content=state["system_prompt"])] + state["messages"]
    
    response = llm_with_tools.invoke(all_msgs)
    return {"messages": [response]}

def execute_tools(state: NarrativeAgentState):
    """
    Custom tool execution node.
    """
    last_msg = state["messages"][-1]
    tool_calls = last_msg.tool_calls
    
    new_messages = []
    new_logs = []
    
    session = session_manager.load_session(state["session_id"])
    player = session.players[0]
    lang = getattr(session, "language", "en")
    
    for tool_call in tool_calls:
        if tool_call["name"] == "ability_check":
            args = tool_call["args"]
            ability = (args.get("ability") or "").lower()
            dc = int(args.get("dc"))
            reason = args.get("reason") or "?"
            
            abilities = getattr(player.character_sheet, "abilities", {}) or {}
            score = int(abilities.get(ability, 10))
            modifier = (score - 10) // 2
            expr = f"1d20{modifier:+d}"
            
            roll_result = roll_dice(expr)
            total = roll_result["total"]
            success = total >= dc
            outcome = "SUCCESS" if success else "FAILURE"
            
            # i18n logs
            t_title = get_text(lang, "dm_log", "check_title")
            t_reason = get_text(lang, "dm_log", "reason")
            t_ability = get_text(lang, "dm_log", "ability")
            t_dc = get_text(lang, "dm_log", "dc")
            t_res = get_text(lang, "dm_log", "result")
            
            log_detail = (
                f"{t_title}:\n"
                f"- {t_reason}: {reason}\n"
                f"- {t_ability}: {ability.capitalize()} (score {score}, mod {modifier:+d})\n"
                f"- {t_dc}: {dc}\n"
                f"- {t_res}: {expr} = {total} -> {outcome}"
            )
            new_logs.append(log_detail)
            
            # Tool Output
            result_json = json.dumps({
                "total": total,
                "success": success,
                "outcome": outcome
            })
            new_messages.append(ToolMessage(tool_call_id=tool_call["id"], content=result_json))
            
    return {
        "messages": new_messages,
        "mechanics_logs": new_logs # Append logs
    }

def parse_output(state: NarrativeAgentState):
    """
    Parse the final AIMessage into JSON structure.
    Since we don't force JSON mode on every step in LangGraph (unlike the loop),
    we might need a final structured output step OR we instruct the LLM to always return JSON.
    
    The current prompt asks for JSON. So the last message content should be JSON.
    """
    last_msg = state["messages"][-1]
    content = last_msg.content
    
    try:
        # Try to find JSON block
        if "```json" in content:
            content = content.split("```json")[1].split("```")[0]
        elif "```" in content:
            content = content.split("```")[1]
            
        data = json.loads(content)
        return {
            "final_narrative": data.get("narrative", ""),
            "transition_to_id": data.get("transition_to_id"),
            "active_mode": None # Will be calculated by router
        }
    except Exception:
        # Fallback
        return {
            "final_narrative": content,
            "transition_to_id": None,
            "active_mode": None
        }

# --- 3. Build Graph ---

def should_continue(state: NarrativeAgentState) -> Literal["execute_tools", "parse_output"]:
    last_msg = state["messages"][-1]
    if last_msg.tool_calls:
        return "execute_tools"
    return "parse_output"

workflow = StateGraph(NarrativeAgentState)

workflow.add_node("load_context", load_context)
workflow.add_node("dm_agent", call_model)
workflow.add_node("execute_tools", execute_tools)
workflow.add_node("parse_output", parse_output)

workflow.set_entry_point("load_context")
workflow.add_edge("load_context", "dm_agent")

workflow.add_conditional_edges(
    "dm_agent",
    should_continue
)

workflow.add_edge("execute_tools", "dm_agent") # Loop back to model
workflow.add_edge("parse_output", END)

narrative_graph = workflow.compile()
