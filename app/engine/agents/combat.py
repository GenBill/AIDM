import json
import os
from typing import Literal, Dict, Any

from langchain_core.messages import SystemMessage, HumanMessage
from langchain_openai import ChatOpenAI
from langgraph.graph import StateGraph, END

from app.engine.state import CombatAgentState
from app.engine.session import session_manager
from app.engine.combat import resolve_attack
from app.config import STORIES_DIR
from app.schemas import DMResponse
from app.engine.i18n import get_text

# --- 1. Nodes ---

def load_combat_context(state: CombatAgentState):
    """Load all necessary data for combat."""
    session_id = state["session_id"]
    session = session_manager.load_session(session_id)
    player = session.players[0]
    lang = getattr(session, "language", "en")
    
    # Load Node
    story_path = STORIES_DIR / session.story_id / "story.json"
    with open(story_path, "r", encoding="utf-8") as f:
        story_data = json.load(f)
    current_node = story_data["nodes"].get(session.current_node_id, {})
    
    # Identify Enemy
    entities = current_node.get("entities", []) or []
    enemies = [e for e in entities if e.get("type") == "monster"]
    
    # If no enemies, we might need a fast-exit path, but for simplicity we handle it in planner or here.
    # If no enemies, we return a special flag or handle in planner.
    
    target_enemy = enemies[0] if enemies else {}
    enemy_name = target_enemy.get("name", "Enemy")
    enemy_stats = target_enemy.get("stats", {}) or {}
    enemy_max_hp = enemy_stats.get("hp_max", 30)
    
    # Calc current HP
    if session.enemy_states is None: session.enemy_states = {}
    enemy_state = session.enemy_states.get(enemy_name, {"damage_taken": 0})
    enemy_dmg = enemy_state.get("damage_taken", 0)
    enemy_current_hp = max(0, enemy_max_hp - enemy_dmg)
    
    # Prepare Player Attacks List for Planner
    player_attacks = []
    for atk in getattr(player.character_sheet, "attacks", []) or []:
        player_attacks.append({
            "name": getattr(atk, "name", "Attack"),
            "bonus": getattr(atk, "bonus", 0),
            "damage": getattr(atk, "damage", "")
        })
        
    # Prepare Context Dicts
    player_data = {
        "name": player.name,
        "hp": player.current_hp,
        "ac": player.character_sheet.ac,
        "attacks": player_attacks
    }
    
    enemy_data = {
        "name": enemy_name,
        "hp": enemy_current_hp,
        "max_hp": enemy_max_hp,
        "ac": enemy_stats.get("ac", 12),
        "actions": enemy_stats.get("actions", [])
    }
    
    # Get last narration
    last_dm = None
    for msg in reversed(session.chat_history):
        if msg.get("role") in ("assistant", "dm"):
            last_dm = msg.get("content")
            break
            
    return {
        "player_data": player_data,
        "enemy_data": enemy_data,
        "last_dm_narration": last_dm,
        "language": lang
    }

def planner_node(state: CombatAgentState):
    """LLM decides actions for both sides."""
    # Check if combat over before planning
    if state["enemy_data"]["hp"] <= 0:
        return {
            "combat_plan": {"combat_state": {"should_end": True, "end_reason": "enemy_dead"}},
            "active_mode": "action"
        }
    
    if os.getenv("OPENAI_API_KEY"):
        llm = ChatOpenAI(model="gpt-4o", temperature=0.1)
    elif os.getenv("DEEPSEEK_API_KEY"):
        llm = ChatOpenAI(
            model="deepseek-chat", 
            api_key=os.getenv("DEEPSEEK_API_KEY"), 
            base_url="https://api.deepseek.com",
            temperature=0.1
        )
    
    PLANNER_PROMPT = """
    You are a STRICT D&D 5e combat planner.
    Return ONLY a JSON object with this schema:
    {
      "player_action": {"kind": "attack"|"cast_spell"|"talk"|"other", "attack_name": "...", "target": "..."},
      "enemy_action": {"kind": "attack"|"other", "action_name": "...", "target": "..."},
      "combat_state": {"should_end": bool, "end_reason": "enemy_dead"|"player_dead"|null}
    }
    """
    
    context = {
        "player": state["player_data"],
        "enemy": state["enemy_data"],
        "last_narration": state["last_dm_narration"],
        "player_input": state["player_input"]
    }
    
    msgs = [
        SystemMessage(content=PLANNER_PROMPT),
        HumanMessage(content=f"Context: {json.dumps(context)}")
    ]
    
    # We ask for JSON object
    response = llm.invoke(msgs)
    content = response.content
    if "```json" in content:
        content = content.split("```json")[1].split("```")[0]
    elif "```" in content:
        content = content.split("```")[1]
        
    try:
        plan = json.loads(content)
    except:
        plan = {}
        
    return {"combat_plan": plan}

def simulator_node(state: CombatAgentState):
    """Execute the plan using Python logic."""
    plan = state.get("combat_plan", {})
    if not plan:
        return {}
        
    player = state["player_data"]
    enemy = state["enemy_data"]
    lang = state["language"]
    
    player_action = plan.get("player_action", {})
    enemy_action = plan.get("enemy_action", {})
    
    logs = []
    p_dmg_taken = 0
    e_dmg_taken = 0
    
    p_result = None
    e_result = None
    
    # 1. Player Attack
    if player_action.get("kind") in ["attack", "cast_spell"]:
        atk_name = player_action.get("attack_name")
        # Find attack stats
        found_atk = next((a for a in player["attacks"] if a["name"] == atk_name), None)
        if found_atk:
            res = resolve_attack(
                attacker_name=player["name"],
                attack_name=atk_name,
                attack_bonus=int(found_atk["bonus"]),
                target_name=enemy["name"],
                target_ac=int(enemy["ac"]),
                damage_dice=found_atk["damage"],
                lang=lang
            )
            if res["is_hit"]:
                e_dmg_taken += res["damage_dealt"]
            logs.append(res["log"])
            p_result = res
            
    # 2. Enemy Attack (if not dead)
    current_e_hp = enemy["hp"] - e_dmg_taken
    if current_e_hp > 0 and enemy_action.get("kind") == "attack":
        act_name = enemy_action.get("action_name")
        # Find enemy action
        found_act = next((a for a in enemy["actions"] if a["name"] == act_name), None)
        # Fallback to first action if planner failed
        if not found_act and enemy["actions"]:
            found_act = enemy["actions"][0]
            
        if found_act and found_act.get("attack_bonus") is not None:
             res = resolve_attack(
                attacker_name=enemy["name"],
                attack_name=found_act["name"],
                attack_bonus=int(found_act["attack_bonus"]),
                target_name=player["name"],
                target_ac=int(player["ac"]),
                damage_dice=found_act["damage_dice"],
                lang=lang
            )
             if res["is_hit"]:
                 p_dmg_taken += res["damage_dealt"]
             logs.append(res["log"])
             e_result = res

    # Summary for Narrator
    summary = {
        "player": {"name": player["name"], "hp_before": player["hp"], "hp_after": max(0, player["hp"] - p_dmg_taken)},
        "enemy": {"name": enemy["name"], "hp_before": enemy["hp"], "hp_after": max(0, enemy["hp"] - e_dmg_taken)},
        "player_action": player_action,
        "enemy_action": enemy_action,
        "results": {"player": p_result, "enemy": e_result},
        "logs": logs
    }
    
    return {
        "round_summary": summary, 
        "mechanics_log": "\n".join(logs)
    }

def narrator_node(state: CombatAgentState):
    """Generate vivid description."""
    summary = state.get("round_summary")
    lang = state["language"]
    
    # If combat ended early (e.g. enemy already dead)
    if not summary:
        if state.get("combat_plan", {}).get("combat_state", {}).get("end_reason") == "enemy_dead":
             msg = get_text(lang, "dm_context", "defeated_msg").format(enemy_name=state["enemy_data"]["name"])
             return {"final_narrative": msg, "active_mode": "action"}
        return {"final_narrative": "Combat logic error.", "active_mode": "fight"}

    if os.getenv("OPENAI_API_KEY"):
        llm = ChatOpenAI(model="gpt-5.1") # Use smarter model for narration
    elif os.getenv("DEEPSEEK_API_KEY"):
        llm = ChatOpenAI(
            model="deepseek-chat", 
            api_key=os.getenv("DEEPSEEK_API_KEY"), 
            base_url="https://api.deepseek.com"
        )
        
    sys_prompt = get_text(lang, "fight_narrator_system")
    msgs = [
        SystemMessage(content=sys_prompt),
        HumanMessage(content=f"Round Summary: {json.dumps(summary, default=str)}")
    ]
    
    resp = llm.invoke(msgs)
    
    # Determine Mode Switch
    active_mode = "fight"
    if summary["player"]["hp_after"] <= 0 or summary["enemy"]["hp_after"] <= 0:
        active_mode = "action"
        
    return {
        "final_narrative": resp.content,
        "active_mode": active_mode
    }
    
def update_session(state: CombatAgentState):
    """Commit changes to DB."""
    session = session_manager.load_session(state["session_id"])
    summary = state.get("round_summary", {})
    
    if summary:
        # Update Player
        session.players[0].current_hp = summary["player"]["hp_after"]
        
        # Update Enemy
        enemy_name = summary["enemy"]["name"]
        dmg_taken = summary["enemy"]["hp_before"] - summary["enemy"]["hp_after"]
        # In session.enemy_states we store total damage taken
        old_state = session.enemy_states.get(enemy_name, {"damage_taken": 0})
        # Calculate TOTAL damage taken from max_hp
        # Since we only have 'hp_after' here, we need to be careful.
        # Actually easier: Max HP - Current HP = Total Damage
        # We need Max HP. It was in state["enemy_data"]["max_hp"].
        # But we don't have direct access to that input here unless we passed it through.
        # Simulator node calculated hp_after.
        
        # Let's trust simulator's calculation for this turn's damage
        damage_this_turn = summary["enemy"]["hp_before"] - summary["enemy"]["hp_after"]
        new_total_damage = old_state["damage_taken"] + damage_this_turn
        session.enemy_states[enemy_name] = {"damage_taken": new_total_damage}
        
    # Append History
    session.chat_history.append({"role": "user", "content": f"[Fight] {state['player_input']}"})
    if state.get("mechanics_log"):
        session.chat_history.append({"role": "data", "content": state["mechanics_log"]})
    session.chat_history.append({"role": "assistant", "content": state.get("final_narrative", "")})
    
    session_manager.save_session(session)
    return {}

# --- 2. Build Graph ---

workflow = StateGraph(CombatAgentState)
workflow.add_node("load_context", load_combat_context)
workflow.add_node("planner", planner_node)
workflow.add_node("simulator", simulator_node)
workflow.add_node("narrator", narrator_node)
workflow.add_node("update_session", update_session)

workflow.set_entry_point("load_context")
workflow.add_edge("load_context", "planner")
workflow.add_edge("planner", "simulator")
workflow.add_edge("simulator", "narrator")
workflow.add_edge("narrator", "update_session")
workflow.add_edge("update_session", END)

combat_graph = workflow.compile()
