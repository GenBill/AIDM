import json
from openai import OpenAI
from app.engine.session import session_manager
from app.schemas import DMResponse
from app.config import STORIES_DIR
# 引入双工具
from app.engine.combat import roll_dice, resolve_attack 
from app.engine.agent_workflow import answer_query

client = OpenAI()

# --- TOOL DEFINITIONS ---
TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "roll_dice",
            "description": "Roll dice for general checks (Skill Checks, Saving Throws). DO NOT use for Combat Attacks.",
            "parameters": {
                "type": "object",
                "properties": {
                    "expr": {"type": "string", "description": "e.g., '1d20+5'"}
                },
                "required": ["expr"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "resolve_attack",
            "description": "Resolve a COMBAT ATTACK. Calculates Hit/Miss and Damage automatically.",
            "parameters": {
                "type": "object",
                "properties": {
                    "attacker_name": {"type": "string"},
                    "attack_name": {"type": "string"},
                    "attack_bonus": {"type": "integer"},
                    "target_name": {"type": "string"},
                    "target_ac": {"type": "integer"},
                    "damage_dice": {"type": "string"}
                },
                "required": ["attacker_name", "attack_name", "attack_bonus", "target_name", "target_ac", "damage_dice"]
            }
        }
    }
]

SYSTEM_PROMPT = """
You are an expert Dungeon Master running a D&D 5e adventure.

Remember what a Dungeon Master do:
1. When the player knows what they want to do
→ Provide the key information they need.
2. When the player doesn't know what to do
→ Guide them toward noticing the key information.
3. When the player takes an unexpected action
→ Find a way to bring them back:
Either prevent them from leaving the sandbox, or
Use another interesting event to lure them back into the intended path.
4. Do not force players to move on, instruct them to move on, unless it is a encounter fight.
### DECISION PROTOCOL
1. **General Checks (Negotiation, Perception, Stealth)**:
   - Call `roll_dice("1d20 + Modifier")`.
   - Compare result vs DC.
   
2. **Combat Attacks**:
   - Call `resolve_attack` with attacker stats and target AC.
   - Determine who attacks whom based on the turn flow.

### RULES
- **Respect Results**: If a check/attack fails, describe the failure and its consequences. Do not fudge rolls.
- **Stats**: Use the Player and Entity stats from Context.

### OUTPUT
1. **Narrative**: Vivid description.
2. **Mechanics Log**: Summarize rolls.
3. **Damage**: Player damage taken.
4. **Transition**: Next scene ID.
"""

class DungeonMasterAI:
    def process_turn(self, session_id: str, player_input: str) -> DMResponse:
        session = session_manager.load_session(session_id)
        player = session.players[0]
        
        story_path = STORIES_DIR / session.story_id / "story.json"
        with open(story_path, "r", encoding="utf-8") as f:
            story_data = json.load(f)
        
        current_node = story_data["nodes"].get(session.current_node_id)
        
        # --- 节奏控制 (Pacing Logic) ---
        session.current_node_turns += 1
        min_turns = current_node.get("min_turns", 2)
        current_node_type = current_node.get("type", "roleplay")
        
        # 预判下一个节点类型
        next_edges = current_node.get('edges', [])
        next_node_type = "unknown"
        if next_edges:
            first_target_id = next_edges[0]['to']
            if first_target_id in story_data["nodes"]:
                next_node_type = story_data["nodes"][first_target_id].get("type", "transition")

        pacing_instruction = ""
        
        if session.current_node_turns < min_turns:
            # 1. 回合未满：按住玩家
            pacing_instruction = f"[PACING: HOLD] Turn {session.current_node_turns}/{min_turns}. Keep player in current scene."
        else:
            # 2. 回合已满：根据情况决定
            if current_node_type == 'encounter':
                # A. 刚打完架：给玩家喘息时间 (Victory Lap)
                pacing_instruction = """
                [PACING: VICTORY LAP] 
                Combat is likely resolved. Do NOT auto-transition.
                Describe the aftermath (loot, silence). Ask "What do you do?".
                Only transition if player explicitly moves on.
                """
            elif next_node_type == 'encounter':
                # B. 下一场是战斗：强制突袭 (AMBUSH) !!!
                pacing_instruction = """
                [PACING: AMBUSH]
                The current scene is over. The NEXT node is a COMBAT ENCOUNTER.
                You MUST trigger the transition NOW.
                Interrupt the player's action with the arrival of the threat (monster/enemy).
                Set `transition_to_id` to the encounter node immediately.
                """
            else:
                # C. 下一场是剧情：自然过渡
                pacing_instruction = "[PACING: GUIDE] Scene complete. Guide player to next area naturally."

        # 构建 Context
        context = f"""
        --- PLAYER ---
        Name: {player.name} | HP: {player.current_hp}
        Stats: {json.dumps(player.character_sheet.abilities.dict())}
        
        --- CURRENT SCENE ---
        Title: {current_node.get('title')}
        Description: {current_node.get('read_aloud')}
        GM Secrets: {current_node.get('gm_guidance')}
        
        --- NEXT SCENE PREVIEW ---
        Next Node Type: {next_node_type}
        Available Exits: {json.dumps(next_edges, indent=2)}
        
        --- INSTRUCTIONS ---
        {pacing_instruction}
        Player says: "{player_input}"
        """

        # 初始化消息
        messages = [
            {"role": "system", "content": SYSTEM_PROMPT},
            *self._sanitize_history(session.chat_history[-6:]),
            {"role": "user", "content": context}
        ]

        # 工具循环
        mechanics_logs = [] 
        total_player_damage_taken = 0

        while True:
            completion = client.chat.completions.create(
                model="gpt-4o-2024-08-06",
                messages=messages,
                tools=TOOLS,
                tool_choice="auto" 
            )
            
            msg = completion.choices[0].message

            if msg.tool_calls:
                messages.append(msg)
                for tool in msg.tool_calls:
                    tool_name = tool.function.name
                    args = json.loads(tool.function.arguments)
                    
                    try:
                        result_content = ""
                        if tool_name == "resolve_attack":
                            result = resolve_attack(
                                attacker_name=args["attacker_name"],
                                attack_name=args["attack_name"],
                                attack_bonus=args["attack_bonus"],
                                target_name=args["target_name"],
                                target_ac=args["target_ac"],
                                damage_dice=args["damage_dice"]
                            )
                            mechanics_logs.append(result["log"])
                            result_content = json.dumps(result)
                            if player.name.lower() in args["target_name"].lower() or args["target_name"].lower() == "player":
                                if result["is_hit"]:
                                    total_player_damage_taken += result["damage_dealt"]

                        elif tool_name == "roll_dice":
                            expr = args.get("expr")
                            result = roll_dice(expr)
                            total = result['total']
                            detail = f"Check: Rolled {expr} => {total}"
                            mechanics_logs.append(detail)
                            result_content = json.dumps(result)

                        messages.append({
                            "role": "tool",
                            "tool_call_id": tool.id,
                            "content": result_content
                        })
                    except Exception as e:
                        messages.append({
                            "role": "tool",
                            "tool_call_id": tool.id,
                            "content": f"Error: {str(e)}"
                        })
            else:
                break

        final_completion = client.beta.chat.completions.parse(
            model="gpt-4o-2024-08-06",
            messages=messages, 
            response_format=DMResponse
        )
        
        dm_decision = final_completion.choices[0].message.parsed
        
        if total_player_damage_taken > 0:
            dm_decision.damage_taken = total_player_damage_taken
            
        if mechanics_logs:
            combined_logs = "\n".join(mechanics_logs)
            if dm_decision.mechanics_log:
                dm_decision.mechanics_log += f"\n[Verified]:\n{combined_logs}"
            else:
                dm_decision.mechanics_log = combined_logs

        session.chat_history.append({"role": "user", "content": player_input})
        if dm_decision.mechanics_log:
            session.chat_history.append({"role": "data", "content": dm_decision.mechanics_log})
        session.chat_history.append({"role": "assistant", "content": dm_decision.narrative})
        
        if dm_decision.damage_taken > 0:
            player.current_hp = max(0, player.current_hp - dm_decision.damage_taken)
            
        if dm_decision.transition_to_id and dm_decision.transition_to_id in story_data["nodes"]:
            session.current_node_id = dm_decision.transition_to_id
            session.current_node_turns = 0
            new_node = story_data["nodes"][dm_decision.transition_to_id]
            welcome_text = f"\n\n[Entered: {new_node.get('title')}]\n" + (new_node.get('read_aloud') or "")
            
            session.chat_history.append({"role": "assistant", "content": welcome_text})
            dm_decision.narrative += welcome_text

        session_manager.save_session(session)
        return dm_decision

    def _sanitize_history(self, history):
        sanitized = []
        for msg in history:
            if msg.get("role") == "data":
                sanitized.append({"role": "system", "content": f"[Previous Log]: {msg['content']}"})
            elif msg.get("role") in ["user", "assistant", "system", "tool"]:
                sanitized.append(msg)
        return sanitized

    def process_query(self, session_id: str, player_input: str) -> DMResponse:
        session = session_manager.load_session(session_id)
        player = session.players[0]
        story_path = STORIES_DIR / session.story_id / "story.json"
        with open(story_path, "r", encoding="utf-8") as f:
            story_data = json.load(f)
        current_node = story_data["nodes"].get(session.current_node_id, {})

        contextual_query = f"""
    You are the AIDND rules/lore assistant. The game is PAUSED.
    Use tools from the local Open5e catalog when needed.
    --- PLAYER ---
    Name: {player.name}
    --- SCENE ---
    Title: {current_node.get("title")}
    Entities: {json.dumps(current_node.get("entities", []), indent=2)}
    --- QUESTION ---
    {player_input}
    """.strip()

        try:
            answer_text = answer_query(contextual_query)
        except Exception as e:
            answer_text = f"Error: {str(e)}"

        session.chat_history.append({"role": "query", "content": player_input})
        session.chat_history.append({"role": "query_answer", "content": answer_text})
        session_manager.save_session(session)

        return DMResponse(narrative=answer_text, damage_taken=0, transition_to_id=None)

ai_dm = DungeonMasterAI()