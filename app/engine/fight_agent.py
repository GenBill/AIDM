# app/engine/fight_agent.py
import json
from openai import OpenAI
from app.engine.session import session_manager
from app.schemas import DMResponse
from app.config import STORIES_DIR
from app.engine.combat import resolve_attack, roll_dice

client = OpenAI()

ENCOUNTER_TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "resolve_attack",
            "description": "Execute a physical attack.",
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
    },
    {
        "type": "function",
        "function": {
            "name": "roll_dice",
            "description": "Roll for Skill Checks or Saving Throws.",
            "parameters": {
                "type": "object",
                "properties": {
                    "expr": {"type": "string"}
                },
                "required": ["expr"]
            }
        }
    }
]

SYSTEM_PROMPT = """
You are an "Encounter Engine" for D&D 5e. 

### TURN LOGIC
1. **Analyze Player Intent**: Attack? Negotiate?
2. **Enemy Reaction**: 
   - If Hostile/Attacked: Attack back.
   - If Negotiating: Respond verbally.

### EXIT CONDITIONS
Set `active_mode` = "action" IF Victory, Resolution, or Escape.

### OUTPUT
- `mechanics_log`: Log attacks/checks.
- `narrative`: Describe exchange.
"""

class FightAgent:
    def process_fight_round(self, session_id: str, player_input: str) -> DMResponse:
        session = session_manager.load_session(session_id)
        player = session.players[0]
        
        story_path = STORIES_DIR / session.story_id / "story.json"
        with open(story_path, "r", encoding="utf-8") as f:
            story_data = json.load(f)
        current_node = story_data["nodes"].get(session.current_node_id, {})
        
        enemies = current_node.get("entities", [])
        target_enemy = enemies[0] if enemies else {}
        enemy_name = target_enemy.get("name", "Enemy")
        enemy_stats = target_enemy.get("stats", {})
        enemy_max_hp = enemy_stats.get("hp_max", 30)
        
        if session.enemy_states is None: session.enemy_states = {}
        enemy_damage_taken = session.enemy_states.get(enemy_name, {}).get("damage_taken", 0)
        current_enemy_hp = enemy_max_hp - enemy_damage_taken

        context = f"""
        --- SCENE ---
        Title: {current_node.get('title')}
        Win Conditions: {current_node.get('gm_guidance')}
        Interactions: {json.dumps(current_node.get('interactions', []), indent=2)}
        
        --- PLAYER ---
        Name: {player.name} | HP: {player.current_hp} | AC: {player.character_sheet.ac}
        Attacks: {json.dumps([a.dict() for a in player.character_sheet.attacks])}
        
        --- ENEMY ---
        Name: {enemy_name} | HP: {current_enemy_hp}/{enemy_max_hp}
        Actions: {json.dumps(enemy_stats.get('actions', []), indent=2)}
        
        --- INPUT ---
        "{player_input}"
        """

        messages = [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": context}
        ]

        mechanics_logs = []
        total_player_damage = 0
        round_enemy_damage = 0

        while True:
            completion = client.chat.completions.create(
                model="gpt-5.1",
                messages=messages,
                tools=ENCOUNTER_TOOLS,
                tool_choice="auto"
            )
            msg = completion.choices[0].message
            
            if msg.tool_calls:
                messages.append(msg)
                for tool in msg.tool_calls:
                    args = json.loads(tool.function.arguments)
                    tool_result_str = ""
                    
                    try:
                        if tool.function.name == "resolve_attack":
                            # === 关键修复：Fight Agent 里的 AC 强制修正 ===
                            target_is_player = (player.name.lower() in args["target_name"].lower()) or (args["target_name"].lower() == "player")
                            if target_is_player:
                                print(f"🛡️ FightAgent overriding AI AC {args.get('target_ac')} with Real AC {player.character_sheet.ac}")
                                args["target_ac"] = player.character_sheet.ac

                            result = resolve_attack(**args)
                            mechanics_logs.append(result["log"])
                            
                            dmg = result["damage_dealt"] if result["is_hit"] else 0
                            if args["attacker_name"] == player.name:
                                round_enemy_damage += dmg
                            else:
                                total_player_damage += dmg
                            
                            tool_result_str = json.dumps(result)

                        elif tool.function.name == "roll_dice":
                            expr = args.get("expr")
                            result = roll_dice(expr)
                            total = result['total']
                            log_entry = f"🎲 Check: Rolled {expr} = **{total}**"
                            mechanics_logs.append(log_entry)
                            tool_result_str = json.dumps(result)

                        messages.append({"role": "tool", "tool_call_id": tool.id, "content": tool_result_str})
                        
                    except Exception as e:
                        messages.append({"role": "tool", "tool_call_id": tool.id, "content": str(e)})
            else:
                break

        final_res = client.beta.chat.completions.parse(
            model="gpt-4o-2024-08-06",
            messages=messages,
            response_format=DMResponse
        )
        dm_response = final_res.choices[0].message.parsed
        
        dm_response.damage_taken = total_player_damage
        if mechanics_logs:
            dm_response.mechanics_log = "\n".join(mechanics_logs)

        if dm_response.damage_taken > 0:
            player.current_hp = max(0, player.current_hp - dm_response.damage_taken)
        
        if round_enemy_damage > 0:
            new_damage = enemy_damage_taken + round_enemy_damage
            session.enemy_states[enemy_name] = {"damage_taken": new_damage}
            if (enemy_max_hp - new_damage) <= 0:
                dm_response.active_mode = "action"
                if "defeat" not in dm_response.narrative.lower():
                    dm_response.narrative += f"\n\n(System: {enemy_name} has been defeated!)"

        session.chat_history.append({"role": "user", "content": f"[Encounter] {player_input}"})
        if dm_response.mechanics_log:
            session.chat_history.append({"role": "data", "content": dm_response.mechanics_log})
        session.chat_history.append({"role": "assistant", "content": dm_response.narrative})

        session_manager.save_session(session)
        return dm_response

fight_agent = FightAgent()