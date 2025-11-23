# app/engine/ai_dm.py
import json
from openai import OpenAI
from app.engine.session import session_manager
from app.schemas import GameSession, DMResponse
from app.config import STORIES_DIR

client = OpenAI()

SYSTEM_PROMPT = """
You are an expert Dungeon Master running a D&D 5e adventure.
Your goal is to immerse the player in the story, manage rules fairly, and drive the narrative forward.

### INSTRUCTIONS
1. **Narrate**: Describe the outcome of the player's action based on the Current Scene and GM Guidance. Be vivid but concise.
2. **Check Transitions**: Look at the "Available Exits/Edges". If the player's action satisfies a transition condition (e.g., "defeat zombies", "move to temple"), trigger the transition by returning the `transition_to_id`.
3. **Manage Health**: If the player takes damage (from traps, attacks, or failure), indicate the amount in `damage_taken`.
4. **Tone**: Serious, atmospheric, yet supportive.

### OUTPUT FORMAT
You must respond with a valid JSON object matching the schema:
{
  "narrative": "You swing your mace...",
  "damage_taken": 0,
  "transition_to_id": null
}
"""

class DungeonMasterAI:
    def process_turn(self, session_id: str, player_input: str) -> DMResponse:
        # 1. 加载 Session
        session = session_manager.load_session(session_id)
        player = session.players[0] # 单人模式.
        
        
        # 2. 加载 Story Node (当前场景信息)
        story_path = STORIES_DIR / session.story_id / "story.json"
        with open(story_path, "r", encoding="utf-8") as f:
            story_data = json.load(f)
            
        current_node = story_data["nodes"].get(session.current_node_id)
        
        # 3. 构建 Context (给 AI 的提示词)
        context = f"""
        --- CURRENT STATE ---
        Player: {player.name} (HP: {player.current_hp}/{player.character_sheet.hp_max})
        Location: {current_node.get('title')}
        Description: {current_node.get('read_aloud')}
        GM Secret Guidance: {current_node.get('gm_guidance')}
        
        --- SCENE ENTITIES (Monsters/NPCs) ---
        {json.dumps(current_node.get('entities', []), indent=2)}
        
        --- AVAILABLE EXITS (Transitions) ---
        {json.dumps(current_node.get('edges', []), indent=2)}
        
        --- PLAYER ACTION ---
        "{player_input}"
        """

        # 4. 调用 GPT-4o (Structured Outputs)
        completion = client.beta.chat.completions.parse(
            model="gpt-4o-2024-08-06",
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                # 注入一点历史对话作为上下文（取最近3轮，避免Token爆炸）
                *session.chat_history[-6:], 
                {"role": "user", "content": context}
            ],
            response_format=DMResponse,
        )
        
        dm_decision = completion.choices[0].message.parsed
        
        # 5. 更新 Session 状态
        # A. 记录对话
        session.chat_history.append({"role": "user", "content": player_input})
        session.chat_history.append({"role": "assistant", "content": dm_decision.narrative})
        
        # B. 扣血
        if dm_decision.damage_taken > 0:
            player.current_hp = max(0, player.current_hp - dm_decision.damage_taken)
            
        # C. 场景跳转
        if dm_decision.transition_to_id:
            # 简单的校验：确保目标节点存在
            if dm_decision.transition_to_id in story_data["nodes"]:
                session.current_node_id = dm_decision.transition_to_id
                # 自动追加新场景的描述到对话中 (可选)
                new_node = story_data["nodes"][dm_decision.transition_to_id]
                welcome_text = f"\n\n[Entered: {new_node.get('title')}]\n" + (new_node.get('read_aloud') or "")
                session.chat_history.append({"role": "assistant", "content": welcome_text})
                
                # 把新场景描述合并返回，让前端一次显示
                dm_decision.narrative += welcome_text

        # 6. 保存存档
        session_manager.save_session(session)
        
        return dm_decision

# 单例
ai_dm = DungeonMasterAI()