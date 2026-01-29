import json
import uuid
from datetime import datetime
from pathlib import Path
from app.config import DATA_DIR, STORIES_DIR
# 引入 schemas
from app.schemas import GameSession, PlayerState, SessionCreateRequest

SESSIONS_DIR = DATA_DIR / "sessions"
SESSIONS_DIR.mkdir(parents=True, exist_ok=True)

class SessionManager:
    def __init__(self):
        pass

    def create_session(self, request: SessionCreateRequest) -> GameSession:
        """初始化一个新的游戏会话"""
        # 1. 读取原始剧本
        story_path = STORIES_DIR / request.story_id / "story.json"
        if not story_path.exists():
            raise FileNotFoundError(f"Story {request.story_id} not found")
        
        with open(story_path, "r", encoding="utf-8") as f:
            story_data = json.load(f)

        # 2. 提取角色
        if request.character_idx >= len(story_data.get("characters", [])):
            raise ValueError("Character index out of range")
        
        raw_char = story_data["characters"][request.character_idx]
        
        # 初始化动态状态
        player_state = PlayerState(
            name=request.player_name, 
            character_sheet=raw_char,
            current_hp=raw_char.get("hp_max", 10),
            inventory=raw_char.get("equipment", [])
        )

        # 3. 寻找起始节点
        nodes = story_data.get("nodes", {})
        start_node_id = list(nodes.keys())[0] if nodes else "start"
        
        # 4. 创建 Session
        session_id = uuid.uuid4().hex[:8]
        now = datetime.now().isoformat()
        
        session = GameSession(
            session_id=session_id,
            story_id=request.story_id,
            title=f"{request.player_name}'s Journey",
            language=request.language,  # Save language preference
            current_node_id=start_node_id,
            current_node_turns=0,  # <--- 初始化为 0
            players=[player_state],
            chat_history=[], 
            created_at=now,
            updated_at=now
        )

        # 5. 保存
        print(f">>> DEBUG: Creating session object: {type(session)}") # 调试信息
        self.save_session(session)
        
        return session

    def load_session(self, session_id: str) -> GameSession:
        path = SESSIONS_DIR / f"{session_id}.json"
        if not path.exists():
            raise FileNotFoundError("Session not found")
        
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return GameSession(**data)

    def save_session(self, session: GameSession):
        """
        将 Session 对象保存为 JSON 文件
        """
        print(f">>> DEBUG: Saving session using Pydantic serialization...") # 调试信息
        
        path = SESSIONS_DIR / f"{session.session_id}.json"
        session.updated_at = datetime.now().isoformat()
        
        with open(path, "w", encoding="utf-8") as f:
            # --- 绝对不要用 json.dump(session.dict()) ---
            # 使用 Pydantic 自带的 model_dump_json() 
            # 它能自动处理嵌套对象序列化
            f.write(session.model_dump_json(indent=2))

    def list_sessions(self):
        sessions = []
        for f in SESSIONS_DIR.glob("*.json"):
            try:
                with open(f, "r", encoding="utf-8") as file:
                    data = json.load(file)
                    sessions.append({
                        "id": data.get("session_id", f.stem),
                        "title": data.get("title", "Untitled"),
                        "updated": data.get("updated_at", "")
                    })
            except:
                continue
        return sorted(sessions, key=lambda x: x["updated"], reverse=True)

session_manager = SessionManager()