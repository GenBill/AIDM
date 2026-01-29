import json
import os
import uuid
from openai import OpenAI
from app.api.deepseek import DeepSeek

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
from app.engine.agent_workflow import answer_query
from app.engine.i18n import get_text

# NEW: Import LangGraph Workflow
from app.engine.agents.narrative import narrative_graph

if os.getenv("OPENAI_API_KEY"):
    MODEL_NAME = "gpt-5.1"
    client = OpenAI()
elif os.getenv("DEEPSEEK_API_KEY"):
    MODEL_NAME = "deepseek-chat" 
    client = DeepSeek()
else:
    raise ValueError("No API key found for OpenAI or DeepSeek")

# --- 初始化 Google Client ---
client_google = None
if GOOGLE_GENAI_AVAILABLE:
    api_key = os.environ.get("GOOGLE_API_KEY")
    if api_key:
        client_google = genai.Client(api_key=api_key)


class DungeonMasterAI:
    def process_turn(self, session_id: str, player_input: str) -> DMResponse:
        """
        AIDM 主逻辑 (Modern Agent Architecture):
        - 使用 LangGraph (narrative_graph) 进行决策循环。
        - 外部 Wrapper 处理副作用 (Session 保存、图片生成、节点跳转)。
        """
        
        # 1. Update Turn Counter (Pacing) BEFORE invoking graph
        session = session_manager.load_session(session_id)
        session.current_node_turns += 1
        session_manager.save_session(session) # Save immediately so Graph sees it
        
        # 2. Invoke LangGraph Agent
        print(f"🤖 [LangGraph] Invoking Narrative Agent for session {session_id}")
        graph_output = narrative_graph.invoke({
            "session_id": session_id,
            "player_input": player_input
        })
        
        final_narrative = graph_output.get("final_narrative", "")
        mechanics_logs = graph_output.get("mechanics_logs", [])
        transition_to_id = graph_output.get("transition_to_id")
        
        # 3. Post-processing (Legacy logic for side effects)
        # Reload session in case graph (or other parallel process?) changed it, though graph is read-mostly for session here.
        # Actually, we can reuse 'session' if we are sure Graph didn't mutate it via session_manager.save_session.
        # But 'execute_tools' DOES load and read session. It doesn't write.
        # Safest is to reload if we are paranoid, but reuse is likely fine here as long as we don't have concurrent writes.
        session = session_manager.load_session(session_id) 
        
        lang = getattr(session, "language", "en")
        story_path = STORIES_DIR / session.story_id / "story.json"
        with open(story_path, "r", encoding="utf-8") as f:
            story_data = json.load(f)
            
        current_node = story_data["nodes"].get(session.current_node_id)
        
        dm_decision = DMResponse(
            narrative=final_narrative,
            mechanics_log="\n".join(mechanics_logs) if mechanics_logs else None,
            damage_taken=0,
            transition_to_id=transition_to_id,
            active_mode=None
        )

        transitioned_to_combat = False

        # --- 节点跳转 & 遭遇战插画 ---
        if dm_decision.transition_to_id and dm_decision.transition_to_id in story_data["nodes"]:
            # Update Session
            session.current_node_id = dm_decision.transition_to_id
            session.current_node_turns = 0
            new_node = story_data["nodes"][dm_decision.transition_to_id]
            
            new_node_type = new_node.get("type")
            new_node_title = new_node.get("title") or "Unknown Scene"
            new_node_read_aloud = new_node.get("read_aloud") or ""

            # 默认：非战斗节点，用原来的进入描述
            welcome_text = f"\n\n[Entered: {new_node_title}]\n{new_node_read_aloud}"
            
            # === 新增：如果是 combat 节点，改成战斗开场白 ===
            if new_node_type == "combat":
                transitioned_to_combat = True
                
                # Fetch Player (re-load to be safe, though session object is fresh)
                player = session.players[0]

                # 简单取第一个敌人
                entities = new_node.get("entities", []) or []
                enemies = [e for e in entities if e.get("type") == "monster"]
                enemy_name = enemies[0].get("name", "enemy") if enemies else "enemy"
                enemy_stats = enemies[0].get("stats", {}) if enemies else {}
                enemy_hp_max = enemy_stats.get("hp_max") or enemy_stats.get("hp") or "unknown"

                # 列举玩家可用攻击（名字 + 伤害骰）
                attacks = getattr(player.character_sheet, "attacks", []) or []
                attack_lines = []
                for atk in attacks:
                    try:
                        atk_name = getattr(atk, "name", None) or atk.get("name", "Attack")
                        atk_damage = getattr(atk, "damage", None) or atk.get("damage", "")
                    except AttributeError:
                        # 如果是 pydantic 模型，不支持 dict 访问，就用属性
                        atk_name = getattr(atk, "name", "Attack")
                        atk_damage = getattr(atk, "damage", "")
                    line = f"- {atk_name} ({atk_damage})"
                    attack_lines.append(line)

                attacks_block = "\n".join(attack_lines) if attack_lines else get_text(lang, "dm_narrative", "no_attacks")

                # 战斗开场白
                t_begins = get_text(lang, "dm_narrative", "combat_begins").format(enemy_name=enemy_name)
                t_hp = ""
                if enemy_hp_max != "unknown":
                    t_hp = get_text(lang, "dm_narrative", "enemy_hp").format(enemy_name=enemy_name, hp=enemy_hp_max)
                
                t_attacks = get_text(lang, "dm_narrative", "attacks_header")
                t_prompt = get_text(lang, "dm_narrative", "combat_prompt")

                welcome_text = f"{t_begins}{t_hp}{t_attacks}{attacks_block}\n\n{t_prompt}"
                
            # 遭遇战节点：生成插画
            if (new_node.get("type") == "encounter" or new_node.get("type") == "combat") and client_google:
                self._generate_encounter_art(session, current_node, new_node, dm_decision.narrative, new_node_type, story_path, story_data)

            # 把进入新节点的欢迎文本写入历史 & narrative
            session.chat_history.append({"role": "assistant", "content": welcome_text})
            dm_decision.narrative += welcome_text

        # --- 根据本轮是否进入 combat 节点 ---
        dm_decision.active_mode = None
        if transitioned_to_combat:
            dm_decision.active_mode = "fight"

        # --- 记录本轮对话 ---
        session.chat_history.append({"role": "user", "content": player_input})
        if dm_decision.mechanics_log:
            session.chat_history.append(
                {"role": "data", "content": dm_decision.mechanics_log}
            )
        session.chat_history.append({"role": "assistant", "content": dm_decision.narrative})

        session_manager.save_session(session)
        return dm_decision

    def _generate_encounter_art(self, session, current_node, new_node, narrative, new_node_type, story_path, story_data):
        """Helper for GenAI Art generation"""
        print(f"🎨 [GenAI] Preparing encounter art for: {new_node.get('title')}")
        try:
            from PIL import Image
            from app.config import BASE_DIR  # 项目根目录

            player = session.players[0]
            
            def load_image(rel_path: str | None, label: str):
                if not rel_path: return None
                clean_path = rel_path.lstrip("/").lstrip("\\")
                if clean_path.startswith("static/"): clean_path = clean_path[len("static/") :]
                abs_path = BASE_DIR / clean_path
                if abs_path.exists():
                     try: return Image.open(abs_path)
                     except: return None
                return None

            entities = new_node.get("entities", []) or []
            enemies = [e for e in entities if e.get("type") == "monster"]
            enemy = enemies[0] if enemies else {}
            enemy_name = enemy.get("name", "Monster") if enemies else "Monster"
            scene_desc = new_node.get("read_aloud") or new_node.get("title") or ""
            player_desc = f"{player.character_sheet.race} {player.character_sheet.class_name}"

            bg_img = load_image(current_node.get("image_path"), "Background")
            player_img = load_image(player.character_sheet.avatar_path, "Player Avatar")
            enemy_img = load_image(enemy.get("image_path"), "Enemy Avatar")
            
            image_prompt = (
                "Fantasy RPG concept art, high quality, cinematic lighting. "
                "Dungeons and Dragons style. All reference characters are DnD characters. "
                f"It is a : {new_node_type} situation. "
                f"Scene description: {scene_desc}. "
                f"What happens now: {narrative}. "
                f"Composition: A fierce {enemy_name} (enemy, see reference) is confronting a "
                f"{player_desc} (player, see reference). "
                "Make them face each other in a dynamic pose, ready for battle. "
                "Make sure the scene only contains these reference characters. "
                "Background: consistent with the provided background reference image. "
                "Atmosphere: tense, dramatic shadows, detailed textures. No text."
            )

            gen_contents: list = [image_prompt]
            if bg_img: gen_contents.append(bg_img)
            if player_img: gen_contents.append(player_img)
            if enemy_img: gen_contents.append(enemy_img)

            response = client_google.models.generate_content(
                model="gemini-2.5-flash-image",
                contents=gen_contents,
                config=types.GenerateContentConfig(
                    response_modalities=["IMAGE"],
                    safety_settings=[types.SafetySetting(category="HARM_CATEGORY_DANGEROUS_CONTENT", threshold="BLOCK_ONLY_HIGH")],
                ),
            )

            generated_image_bytes = None
            try:
                for part in response.candidates[0].content.parts:
                    if getattr(part, "inline_data", None):
                        generated_image_bytes = part.inline_data.data
                        break
            except Exception: pass

            if generated_image_bytes:
                encounter_images_dir = STORIES_DIR / session.story_id / "images" / "encounters"
                os.makedirs(encounter_images_dir, exist_ok=True)
                image_filename = f"gen_{uuid.uuid4().hex[:8]}.png"
                with open(encounter_images_dir / image_filename, "wb") as f_img:
                    f_img.write(generated_image_bytes)

                web_path = f"/static/data/stories/{session.story_id}/images/encounters/{image_filename}"
                story_data["nodes"][new_node["id"]]["image_path"] = web_path
                with open(story_path, "w", encoding="utf-8") as f:
                    json.dump(story_data, f, indent=2, ensure_ascii=False)
                print(f"   ✅ [GenAI] Image saved to: {web_path}")
        except Exception as e:
            print(f"   ❌ [GenAI] Error: {e}")

    def _sanitize_history(self, history):
        # Kept for compatibility if used elsewhere, but Narrative Graph handles its own history sanitization now.
        return history

    def process_query(self, session_id: str, player_input: str) -> DMResponse:
        """
        规则 / 背景问答通道：不改变节点，也不改 HP，只回答问题。
        """
        session = session_manager.load_session(session_id)
        lang = getattr(session, "language", "en") 
        try:
            answer_text = answer_query(player_input, lang=lang) 
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
