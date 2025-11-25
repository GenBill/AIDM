import json
import os
import uuid
from openai import OpenAI

# --- 新增：Google GenAI 依赖 ---
try:
    from google import genai
    from google.genai import types
    GOOGLE_GENAI_AVAILABLE = True
except ImportError:
    GOOGLE_GENAI_AVAILABLE = False
    print("⚠️ Google GenAI SDK not found. Images will not generate.")

from app.engine.session import session_manager
from app.schemas import DMResponse
from app.config import STORIES_DIR
from app.engine.combat import roll_dice, resolve_attack 
from app.engine.agent_workflow import answer_query

client = OpenAI()

# --- 新增：初始化 Google Client ---
client_google = None
if GOOGLE_GENAI_AVAILABLE:
    api_key = os.environ.get("GOOGLE_API_KEY")
    if api_key:
        client_google = genai.Client(api_key=api_key)

# --- TOOL DEFINITIONS ---
TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "roll_dice",
            "description": "Roll dice for general checks (Skill Checks, Saving Throws).",
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
            "description": "Resolve a COMBAT ATTACK. Calculates Hit/Miss and Damage.",
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

# --- PROMPT (保持你的原版) ---
SYSTEM_PROMPT = """
You are an expert Dungeon Master running a D&D 5e adventure.

### MODE CONTROL (CRITICAL)
You control the user interface state via the `active_mode` field.
1. **"action" (Default)**: Exploration, dialogue, negotiation, and descriptions. 
   - **Even if an enemy appears**, keep it in "action" mode until violence actually erupts.
2. **"fight"**: Set this ONLY when **Initiative is rolled** or **Attacks are exchanged**.
   - Use this when negotiation fails or the player chooses violence.
3. **null**: Maintain the current mode.

### RULES
1. **Narrative**: Be vivid. When entering a new scene, describe it first.
2. **Dice**: Call tools for uncertain outcomes.
3. **Transitions**: Move story based on logic.

### OUTPUT FORMAT (JSON)
{
  "narrative": "...",
  "mechanics_log": "...",
  "damage_taken": 0,
  "transition_to_id": "node_id or null",
  "active_mode": "fight | action | null" 
}
"""

class DungeonMasterAI:
    def process_turn(self, session_id: str, player_input: str) -> DMResponse:
        session = session_manager.load_session(session_id)
        player = session.players[0]
        
        story_path = STORIES_DIR / session.story_id / "story.json"
        with open(story_path, "r", encoding="utf-8") as f:
            story_data = json.load(f)
        
        current_node = story_data["nodes"].get(session.current_node_id)
        
        # --- 节奏控制 (保持你的原版) ---
        session.current_node_turns += 1
        min_turns = current_node.get("min_turns", 1)
        
        next_edges = current_node.get('edges', [])
        next_node_type = "unknown"
        if next_edges:
            first_target_id = next_edges[0]['to']
            if first_target_id in story_data["nodes"]:
                next_node_type = story_data["nodes"][first_target_id].get("type", "transition")

        pacing_instruction = ""
        if session.current_node_turns < min_turns:
            pacing_instruction = f"[PACING] Player has spent {session.current_node_turns}/{min_turns} turns here. MUST staying unless PLAYER ask for moving."
        else:
            pacing_instruction = f"[PACING] Scene complexity met. You may transition to the next node ({next_node_type}) if the story flows there."

        # 构建 Context
        context = f"""
        --- PLAYER ---
        Name: {player.name} | HP: {player.current_hp}
        
        --- CURRENT SCENE ---
        Title: {current_node.get('title')} ({current_node.get('type')})
        Description: {current_node.get('read_aloud')}
        GM Secrets: {current_node.get('gm_guidance')}
        
        --- EXITS ---
        {json.dumps(next_edges, indent=2)}
        
        --- INSTRUCTIONS ---
        {pacing_instruction}
        **Reminder**: If you transition to an Encounter, describe the threat appearing, but keep `active_mode`="action" initially. Only switch to "fight" if combat starts immediately.
        Player says: "{player_input}"
        """

        messages = [
            {"role": "system", "content": SYSTEM_PROMPT},
            *self._sanitize_history(session.chat_history[-6:]),
            {"role": "user", "content": context}
        ]

        mechanics_logs = [] 
        total_player_damage_taken = 0

        # --- 工具循环 (保持你的原版) ---
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

                        messages.append({"role": "tool", "tool_call_id": tool.id, "content": result_content})
                    except Exception as e:
                        messages.append({"role": "tool", "tool_call_id": tool.id, "content": f"Error: {str(e)}"})
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

        # --- 状态更新 (此处插入图片生成逻辑) ---
        if dm_decision.transition_to_id and dm_decision.transition_to_id in story_data["nodes"]:
            session.current_node_id = dm_decision.transition_to_id
            session.current_node_turns = 0
            new_node = story_data["nodes"][dm_decision.transition_to_id]
            
            welcome_text = f"\n\n[Entered: {new_node.get('title')}]\n" + (new_node.get('read_aloud') or "")
            
            # === 插入：遭遇战图片生成逻辑 ===
            if new_node.get("type") == "encounter" and client_google:
                print(f"🎨 Generating encounter art for: {new_node.get('title')}")
                try:
                    enemy_name = new_node.get("entities", [{}])[0].get("name", "Monster")
                    scene_desc = new_node.get("read_aloud") or new_node.get("title")
                    player_desc = f"{player.character_sheet.race} {player.character_sheet.class_name}"
                    
                    image_prompt = (
                        f"Fantasy RPG concept art, high quality, cinematic lighting. "
                        f"Scene: {scene_desc}. "
                        f"Foreground Action: A {player_desc} confronting a {enemy_name}. "
                        f"Atmosphere: Tense, dramatic shadows, detailed textures. "
                        f"No text, no watermark, no modern objects."
                    )

                    # ✅ 新版：用 Gemini Image 模型直接生成图片
                    response = client_google.models.generate_content(
                        model="gemini-2.5-flash-image",
                        contents=image_prompt,
                        config=types.GenerateContentConfig(
                            response_modalities=["IMAGE"],
                            safety_settings=[
                                types.SafetySetting(
                                    category="HARM_CATEGORY_DANGEROUS_CONTENT",
                                    threshold="BLOCK_ONLY_HIGH"
                                )
                            ]
                        )
                    )

                    # 取图片 bytes
                    generated_image_bytes = None
                    try:
                        for part in response.candidates[0].content.parts:
                            if getattr(part, "inline_data", None) and part.inline_data.mime_type.startswith("image/"):
                                generated_image_bytes = part.inline_data.data
                                break
                    except Exception:
                        generated_image_bytes = None
                    
                    if generated_image_bytes:
                        # 1. 确保目录存在
                        encounter_images_dir = STORIES_DIR / session.story_id / "images" / "encounters"
                        os.makedirs(encounter_images_dir, exist_ok=True)
                        
                        # 2. 保存图片
                        image_filename = f"gen_{uuid.uuid4().hex[:8]}.png"
                        image_full_path = encounter_images_dir / image_filename
                        with open(image_full_path, "wb") as f_img:
                            f_img.write(generated_image_bytes)
                        
                        # 3. 更新 JSON
                        web_path = f"/static/data/stories/{session.story_id}/images/encounters/{image_filename}"
                        story_data["nodes"][dm_decision.transition_to_id]["image_path"] = web_path
                        
                        with open(story_path, "w", encoding="utf-8") as f:
                            json.dump(story_data, f, indent=2, ensure_ascii=False)
                        print(f"✅ Image generated: {web_path}")

                except Exception as e:
                    print(f"❌ Google Image Gen Error: {e}")
            # =================================

            session.chat_history.append({"role": "assistant", "content": welcome_text})
            dm_decision.narrative += welcome_text
            
            # 删除了之前的 active_mode 强制切换逻辑，完全听从 AI

        session.chat_history.append({"role": "user", "content": player_input})
        if dm_decision.mechanics_log:
            session.chat_history.append({"role": "data", "content": dm_decision.mechanics_log})
        session.chat_history.append({"role": "assistant", "content": dm_decision.narrative})
        
        if dm_decision.damage_taken > 0:
            player.current_hp = max(0, player.current_hp - dm_decision.damage_taken)

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
