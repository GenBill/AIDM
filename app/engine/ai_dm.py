import json
import os
import uuid
from openai import OpenAI

# --- Google GenAI 依赖（用于生成遭遇战插画，可选） ---
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
from app.engine.combat import roll_dice  # ✅ 只保留骰子函数
from app.engine.agent_workflow import answer_query

client = OpenAI()

# --- 初始化 Google Client ---
client_google = None
if GOOGLE_GENAI_AVAILABLE:
    api_key = os.environ.get("GOOGLE_API_KEY")
    if api_key:
        client_google = genai.Client(api_key=api_key)

# --- TOOL DEFINITIONS: 只保留非战斗的 roll_dice ---
TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "roll_dice",
            "description": (
                "Roll dice for non-combat checks such as ability checks, "
                "skill checks, saving throws, or any uncertain narrative outcome. "
                "Do NOT use this to resolve full attack/damage combat rounds; "
                "those are handled by a separate combat agent."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "expr": {
                        "type": "string",
                        "description": "Dice expression, e.g. '1d20+5', '2d6+3'."
                    }
                },
                "required": ["expr"]
            }
        }
    }
]

# --- SYSTEM PROMPT ---
SYSTEM_PROMPT = """
You are an expert Dungeon Master running a D&D 5e adventure.

### YOUR RESPONSIBILITY
You are responsible for:
- Narrative description and roleplay.
- Scene pacing and node transitions in the story graph.
- Light, non-combat dice checks (ability checks, skill checks, saving throws, etc.).
- Controlling the UI mode via `active_mode`:
  - "action"  : exploration / dialogue / pre-combat tension.
  - "fight"   : when actual combat begins (initiative is rolled, or attacks are clearly exchanged).
  - null      : keep the UI mode unchanged.

You are **NOT** responsible for:
- Detailed combat math for each round.
- Applying damage to HP or tracking exact HP values.
- Managing initiative order or turn-by-turn combat resolution.

All detailed combat (attack rolls, damage, HP updates, enemy HP, etc.)
is handled by a separate **combat agent** on the `/fight` endpoint.

### MODE CONTROL (VERY IMPORTANT)
You control the `active_mode` field in your JSON output:

- Use `"action"` for:
  - Normal exploration and dialogue.
  - Scenes where enemies appear but combat has NOT yet actually started.
  - Pre-combat threats, warnings, standoffs, negotiations.

- Use `"fight"` ONLY when:
  - The player clearly chooses violence (e.g., "I attack the goblin", "I shoot my bow", "I cast Fire Bolt at it"),
  - OR an enemy clearly initiates combat (e.g., "The goblins charge and attack"),
  - AND it is appropriate to enter structured combat.

- Use `null` when:
  - You do NOT want to change the current UI mode.
  - For example, small intermediate turns inside the same action mode.

When you set `"fight"`, you are signaling:
> "Switch the UI to combat mode; the combat agent will now handle detailed attacks."

You may still narrate what the start of combat looks like ("They draw steel, blades clash..."),
but do NOT attempt to resolve every attack and damage yourself.

### RULES
1. **Narrative**:
   - Be vivid and grounded in the current node's description and GM guidance.
   - When entering a new scene, briefly describe the environment, key NPCs/monsters, and immediate sensory details.
2. **Dice**:
   - Use the `roll_dice` tool for uncertain non-combat outcomes (skill checks, saving throws, etc.).
   - Log these results in `mechanics_log`.
3. **Transitions**:
   - Use `transition_to_id` only when it logically follows to move to another node.
   - Respect pacing instructions: if the scene has not yet met its minimum turns, stay unless the PLAYER clearly insists on leaving or forcing a transition.
4. **Combat Handoff**:
   - You can describe threats, weapons being drawn, and the first moments of battle.
   - Set `active_mode` to `"fight"` when combat truly begins.
   - Do NOT apply HP changes yourself; leave `damage_taken` as 0 or only very minor narrative chip damage if absolutely necessary.

### OUTPUT FORMAT (JSON)
You MUST always return a JSON object matching this schema:

{
  "narrative": "What you say to the player, describing the scene and consequences.",
  "mechanics_log": "Any dice or mechanical notes. Can be empty string if nothing to log.",
  "damage_taken": 0,
  "transition_to_id": "node_id or null",
  "active_mode": "fight | action | null"
}

- `damage_taken`: For you, this should normally stay 0. HP changes are mainly the combat agent's job.
- `active_mode`:
  - "action": ensure the UI is in exploration/dialogue mode.
  - "fight" : switch the UI into combat mode.
  - null    : do not change the current mode.
"""


class DungeonMasterAI:
    def process_turn(self, session_id: str, player_input: str) -> DMResponse:
        """
        AIDM 主逻辑：
        - 负责叙事 / 节奏 / 节点跳转 / 遭遇战插画
        - 可以通过 active_mode = 'action'|'fight' 控制前端 Tab 切换
        - 不再负责详细战斗结算（attack / damage）
        """
        session = session_manager.load_session(session_id)
        player = session.players[0]

        story_path = STORIES_DIR / session.story_id / "story.json"
        with open(story_path, "r", encoding="utf-8") as f:
            story_data = json.load(f)

        current_node = story_data["nodes"].get(session.current_node_id)

        # --- 节奏控制：计算当前节点轮数 ---
        session.current_node_turns += 1
        min_turns = current_node.get("min_turns", 1)

        next_edges = current_node.get("edges", [])
        next_node_type = "unknown"
        if next_edges:
            first_target_id = next_edges[0]["to"]
            if first_target_id in story_data["nodes"]:
                next_node_type = story_data["nodes"][first_target_id].get("type", "transition")

        if session.current_node_turns < min_turns:
            pacing_instruction = (
                f"[PACING] Player has spent {session.current_node_turns}/{min_turns} turns in this scene. "
                f"Stay in this node unless the PLAYER clearly asks to move on or leave."
            )
        else:
            pacing_instruction = (
                f"[PACING] Scene complexity requirement met. "
                f"You MAY transition to the next node ({next_node_type}) if it feels natural for the story."
            )

        # --- 构建上下文给 LLM ---
        MODEL_NAME = "gpt-5.1" 
        context = f"""
        --- PLAYER ---
        Name: {player.name} | HP: {player.current_hp}

        --- CURRENT SCENE ---
        Title: {current_node.get('title')} ({current_node.get('type')})
        Description: {current_node.get('read_aloud')}
        GM Secrets: {current_node.get('gm_guidance')}

        --- EXITS ---
        {json.dumps(next_edges, indent=2, ensure_ascii=False)}

        --- INSTRUCTIONS ---
        {pacing_instruction}

        - You may lead into encounters and describe enemies.
        - When combat truly begins (attacks, initiative), set `active_mode` to "fight" so the combat agent can take over.
        - Do NOT apply HP changes yourself; combat details are handled elsewhere.

        Player says: "{player_input}"
        """

        messages = [
            {"role": "system", "content": SYSTEM_PROMPT},
            *self._sanitize_history(session.chat_history[-6:]),
            {"role": "user", "content": context},
        ]

        mechanics_logs: list[str] = []

        # --- 工具循环（只处理 roll_dice） ---
        while True:
            completion = client.chat.completions.create(
                model=MODEL_NAME,
                messages=messages,
                tools=TOOLS,
                tool_choice="auto",
            )
            msg = completion.choices[0].message

            if msg.tool_calls:
                messages.append(msg)
                for tool in msg.tool_calls:
                    tool_name = tool.function.name
                    args = json.loads(tool.function.arguments)
                    try:
                        result_content = ""
                        if tool_name == "roll_dice":
                            expr = args.get("expr")
                            result = roll_dice(expr)
                            total = result["total"]
                            detail = f"Check: Rolled {expr} => {total}"
                            mechanics_logs.append(detail)
                            result_content = json.dumps(result, ensure_ascii=False)

                        messages.append(
                            {
                                "role": "tool",
                                "tool_call_id": tool.id,
                                "content": result_content,
                            }
                        )
                    except Exception as e:
                        messages.append(
                            {
                                "role": "tool",
                                "tool_call_id": tool.id,
                                "content": f"Error: {str(e)}",
                            }
                        )
            else:
                break

        # --- 解析最终 DM 决策 ---
        final_completion = client.beta.chat.completions.parse(
            model=MODEL_NAME,
            messages=messages,
            response_format=DMResponse,
        )
        dm_decision: DMResponse = final_completion.choices[0].message.parsed

        # AIDM 不负责扣血，通常保持 damage_taken = 0
        if dm_decision.damage_taken is None:
            dm_decision.damage_taken = 0

        # 合并 mechanics_log（骰子日志）
        if mechanics_logs:
            combined_logs = "\n".join(mechanics_logs)
            if dm_decision.mechanics_log:
                dm_decision.mechanics_log += f"\n[Verified]:\n{combined_logs}"
            else:
                dm_decision.mechanics_log = combined_logs

        # --- 节点跳转 & 遭遇战插画 ---
        if dm_decision.transition_to_id and dm_decision.transition_to_id in story_data["nodes"]:
            session.current_node_id = dm_decision.transition_to_id
            session.current_node_turns = 0
            new_node = story_data["nodes"][dm_decision.transition_to_id]

            welcome_text = (
                f"\n\n[Entered: {new_node.get('title')}]\n" + (new_node.get("read_aloud") or "")
            )

            # 遭遇战节点：生成插画（仍然不处理战斗逻辑）
            if new_node.get("type") == "encounter" and client_google:
                print(f"🎨 [GenAI] Preparing encounter art for: {new_node.get('title')}")
                try:
                    from PIL import Image
                    from app.config import BASE_DIR  # 项目根目录

                    def load_image(rel_path: str | None, label: str):
                        if not rel_path:
                            print(f"   ⚠️ [Image Load] No path provided for {label}")
                            return None

                        clean_path = rel_path.lstrip("/").lstrip("\\")
                        if clean_path.startswith("static/"):
                            clean_path = clean_path[len("static/") :]
                        if clean_path.startswith("static\\"):
                            clean_path = clean_path[len("static\\") :]

                        abs_path = BASE_DIR / clean_path
                        print(f"   🔍 [Image Load] Trying to load {label} from: {abs_path}")

                        if abs_path.exists():
                            try:
                                img = Image.open(abs_path)
                                print(f"   ✅ [Image Load] Loaded {label} successfully.")
                                return img
                            except Exception as e:
                                print(f"   ❌ [Image Load] Failed to open {label}: {e}")
                                return None
                        else:
                            print(f"   ❌ [Image Load] File NOT FOUND: {abs_path}")
                            return None

                    # 1. 收集素材
                    enemy = (new_node.get("entities") or [{}])[0]
                    enemy_name = enemy.get("name", "Monster")
                    scene_desc = new_node.get("read_aloud") or new_node.get("title") or ""
                    player_desc = f"{player.character_sheet.race} {player.character_sheet.class_name}"

                    # 2. 加载参考图
                    print("   --- Loading Reference Images ---")
                    bg_img = load_image(current_node.get("image_path"), "Background")
                    player_img = load_image(player.character_sheet.avatar_path, "Player Avatar")
                    enemy_img = load_image(enemy.get("image_path"), "Enemy Avatar")

                    # 3. 构建 Prompt
                    image_prompt = (
                        "Fantasy RPG concept art, high quality, cinematic lighting. "
                        "Dungeons and Dragons style. All reference characters are DnD characters. "
                        f"Scene description: {scene_desc}. "
                        f"Composition: A fierce {enemy_name} (enemy, see reference) is confronting a "
                        f"{player_desc} (player, see reference). "
                        "Make them face each other in a dynamic pose, ready for battle. "
                        "Make sure the scene only contains these reference characters. "
                        "Background: consistent with the provided background reference image. "
                        "Atmosphere: tense, dramatic shadows, detailed textures. No text."
                    )

                    # 4. 打包内容
                    gen_contents: list = [image_prompt]
                    loaded_count = 0
                    if bg_img:
                        gen_contents.append(bg_img)
                        loaded_count += 1
                    if player_img:
                        gen_contents.append(player_img)
                        loaded_count += 1
                    if enemy_img:
                        gen_contents.append(enemy_img)
                        loaded_count += 1

                    print(f"   🚀 [GenAI] Sending request with {loaded_count} reference images...")

                    # 5. 调用 Google GenAI
                    response = client_google.models.generate_content(
                        model="gemini-2.5-flash-image",
                        contents=gen_contents,
                        config=types.GenerateContentConfig(
                            response_modalities=["IMAGE"],
                            safety_settings=[
                                types.SafetySetting(
                                    category="HARM_CATEGORY_DANGEROUS_CONTENT",
                                    threshold="BLOCK_ONLY_HIGH",
                                )
                            ],
                        ),
                    )

                    # 6. 解析并保存结果
                    generated_image_bytes = None
                    try:
                        for part in response.candidates[0].content.parts:
                            if getattr(part, "inline_data", None) and part.inline_data.mime_type.startswith(
                                "image/"
                            ):
                                generated_image_bytes = part.inline_data.data
                                break
                    except Exception:
                        generated_image_bytes = None

                    if generated_image_bytes:
                        encounter_images_dir = (
                            STORIES_DIR / session.story_id / "images" / "encounters"
                        )
                        os.makedirs(encounter_images_dir, exist_ok=True)

                        image_filename = f"gen_{uuid.uuid4().hex[:8]}.png"
                        image_full_path = encounter_images_dir / image_filename
                        with open(image_full_path, "wb") as f_img:
                            f_img.write(generated_image_bytes)

                        web_path = (
                            f"/static/data/stories/{session.story_id}/images/encounters/{image_filename}"
                        )
                        story_data["nodes"][dm_decision.transition_to_id]["image_path"] = web_path

                        with open(story_path, "w", encoding="utf-8") as f:
                            json.dump(story_data, f, indent=2, ensure_ascii=False)
                        print(f"   ✅ [GenAI] Image saved to: {web_path}")
                    else:
                        print("   ⚠️ [GenAI] API returned no image data.")
                except Exception as e:
                    print(f"   ❌ [GenAI] Critical Error: {e}")

            # 把进入新节点的欢迎文本写入历史 & narrative
            session.chat_history.append({"role": "assistant", "content": welcome_text})
            dm_decision.narrative += welcome_text

        # --- 记录本轮对话 ---
        session.chat_history.append({"role": "user", "content": player_input})
        if dm_decision.mechanics_log:
            session.chat_history.append(
                {"role": "data", "content": dm_decision.mechanics_log}
            )
        session.chat_history.append({"role": "assistant", "content": dm_decision.narrative})

        # ❌ 不在这里修改 HP（战斗 agent 在 /fight 里负责）

        session_manager.save_session(session)
        return dm_decision

    def _sanitize_history(self, history):
        """
        把历史里的 data 日志重新包装成 system，其他 role 原样保留。
        这样 LLM 可以看到之前的 mechanic 日志，但不会把它当成用户输入。
        """
        sanitized = []
        for msg in history:
            if msg.get("role") == "data":
                sanitized.append(
                    {"role": "system", "content": f"[Previous Log]: {msg['content']}"}
                )
            elif msg.get("role") in ["user", "assistant", "system", "tool"]:
                sanitized.append(msg)
        return sanitized

    def process_query(self, session_id: str, player_input: str) -> DMResponse:
        """
        规则 / 背景问答通道：不改变节点，也不改 HP，只回答问题。
        """
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
    Entities: {json.dumps(current_node.get("entities", []), indent=2, ensure_ascii=False)}

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

        return DMResponse(
            narrative=answer_text,
            damage_taken=0,
            transition_to_id=None,
        )


ai_dm = DungeonMasterAI()
