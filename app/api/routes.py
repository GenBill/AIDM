# app/api/routes.py
from fastapi import APIRouter, UploadFile, File, Form, HTTPException
from typing import List, Optional, Union
import json
import shutil
import uuid
from pathlib import Path

from app.schemas import CharacterSheet, StoryCreateRequest, StoryResponse
from app.services.story_generator import generate_story_from_text
# 引入新的函数名
from app.services.pdf_service import parse_character_images
from app.services.pdf_service import parse_monster_image 
from app.config import STORIES_DIR
from app.schemas import SessionCreateRequest, GameSession
from app.engine.session import session_manager
from app.engine.ai_dm import ai_dm
from app.schemas import GameActionRequest, DMResponse
router = APIRouter()

# ==========================================
# 1. 剧本管理 (Create & List & Get)
# ==========================================

@router.post("/stories/create", response_model=StoryResponse)
def create_story(request: StoryCreateRequest):
    # 1. 准备目录结构: data/stories/{uuid}/
    story_id = str(uuid.uuid4())[:8]
    story_folder = STORIES_DIR / story_id
    story_folder.mkdir(parents=True, exist_ok=True)
    (story_folder / "images" / "enemies").mkdir(parents=True, exist_ok=True)
    (story_folder / "images" / "characters").mkdir(parents=True, exist_ok=True)

    # 2. 生成逻辑
    print(f"Using Dungeon Architect Prompt to generate: {request.title}...")
    try:
        story_graph_data = generate_story_from_text(request.raw_script)
    except Exception as e:
        # 发生错误时清理文件夹，避免留下空壳
        shutil.rmtree(story_folder)
        raise HTTPException(status_code=500, detail=f"LLM Generation Failed: {str(e)}")
    
    # 3. 补充元数据
    story_graph_data["id"] = story_id
    story_graph_data["title"] = request.title
    if "characters" not in story_graph_data:
        story_graph_data["characters"] = []
    
    # 4. 保存
    file_path = story_folder / "story.json"
    with open(file_path, "w", encoding="utf-8") as f:
        json.dump(story_graph_data, f, indent=2, ensure_ascii=False)

    # 注意：这里 node_count 处理字典和列表的情况
    nodes = story_graph_data.get("nodes", {})
    count = len(nodes) if isinstance(nodes, (list, dict)) else 0

    return StoryResponse(
        id=story_id,
        title=request.title,
        node_count=count,
        file_path=str(file_path)
    )

@router.get("/stories")
def list_stories():
    """
    修正版：递归查找所有子文件夹里的 story.json
    """
    results = []
    if not STORIES_DIR.exists():
        STORIES_DIR.mkdir(parents=True)
        
    # 使用 rglob (recursive glob) 查找所有子目录下的 story.json
    for f in STORIES_DIR.rglob("story.json"):
        try:
            d = json.loads(f.read_text(encoding="utf-8"))
            results.append({
                "id": d.get("id"), 
                "title": d.get("title", "Untitled Story")
            })
        except Exception as e:
            print(f"Error loading {f}: {e}")
            continue
            
    return results

@router.get("/stories/{story_id}")
def get_story_details(story_id: str):
    file_path = STORIES_DIR / story_id / "story.json"
    if not file_path.exists():
        raise HTTPException(status_code=404, detail="Story not found")
    
    with open(file_path, "r", encoding="utf-8") as f:
        return json.load(f)

# ==========================================
# 2. 工具接口 (Tools)
# ==========================================

@router.post("/tools/parse-pdf", response_model=CharacterSheet)
async def parse_character_pdf(
    file: UploadFile = File(...),
    background_info: Optional[str] = Form(None)
):
    content = await file.read()
    
    if file.filename.endswith(".pdf"):
        text = extract_text_from_pdf(content)
    else:
        # 简单的图片占位处理
        text = "Image parsing requires vision model."

    # 这一步会调用 OpenAI Structured Outputs
    data = parse_character_data(text, user_context=background_info or "")
    return data

# ==========================================
# 3. 资源上传 (Images & Characters)
# ==========================================

@router.post("/stories/{story_id}/enemies/upload-image")
async def upload_enemy_image(
    story_id: str, 
    enemy_name: str = Form(...), 
    # 1. 用于解析数据的图片 (Stat Block)
    stat_block: Optional[UploadFile] = File(None),
    # 2. 用于展示的头像 (Avatar)
    avatar: Optional[UploadFile] = File(None),
    # 3. 额外背景信息
    info: Optional[str] = Form(None)
):
    story_folder = STORIES_DIR / story_id
    json_path = story_folder / "story.json"
    
    if not json_path.exists():
        raise HTTPException(404, "Story not found")

    # 准备目录
    enemy_img_folder = story_folder / "images" / "enemies"
    enemy_img_folder.mkdir(parents=True, exist_ok=True)

    # --- A. 处理 Avatar (视觉头像) ---
    avatar_web_path = None
    if avatar:
        avatar_content = await avatar.read()
        ext = Path(avatar.filename).suffix
        # 命名：avatar_怪物名_uuid.png
        safe_name = f"avatar_{enemy_name.replace(' ', '_')}_{uuid.uuid4().hex[:6]}{ext}"
        save_path = enemy_img_folder / safe_name
        
        with open(save_path, "wb") as f:
            f.write(avatar_content)
        
        avatar_web_path = f"/static/data/stories/{story_id}/images/enemies/{safe_name}"

    # --- B. 处理 Stat Block (数值解析) ---
    parsed_stats = None
    stat_block_web_path = None
    
    if stat_block:
        stat_content = await stat_block.read()
        ext = Path(stat_block.filename).suffix
        safe_name = f"stat_{enemy_name.replace(' ', '_')}_{uuid.uuid4().hex[:6]}{ext}"
        save_path = enemy_img_folder / safe_name
        
        with open(save_path, "wb") as f:
            f.write(stat_content)
            
        stat_block_web_path = f"/static/data/stories/{story_id}/images/enemies/{safe_name}"
        
        # 调用 AI 解析
        try:
            monster_sheet = parse_monster_image(stat_content, user_context=info or "")
            # 转为 Dict
            parsed_stats = monster_sheet.model_dump() if hasattr(monster_sheet, "model_dump") else monster_sheet
            # 把原图路径也存进 stats 里
            parsed_stats["file_path"] = stat_block_web_path
        except Exception as e:
            print(f"Stat Block parsing failed: {e}")
            # 如果解析失败，这里可以选择报错，或者忽略错误只存图片
            # raise HTTPException(500, detail=f"Parsing failed: {e}")

    # --- C. 更新 JSON ---
    with open(json_path, "r", encoding="utf-8") as f:
        story_data = json.load(f)

    updated = False
    nodes = story_data.get("nodes", {})
    iterable_nodes = nodes.values() if isinstance(nodes, dict) else nodes

    for node in iterable_nodes:
        entities = node.get("entities", [])
        for entity in entities:
            if entity.get("name") == enemy_name:
                updated = True
                
                # 1. 更新头像路径
                if avatar_web_path:
                    entity["image_path"] = avatar_web_path
                # 如果没传 avatar 但传了 stat_block，且之前没头像，这就暂用 stat_block 当头像
                elif stat_block_web_path and "image_path" not in entity:
                    entity["image_path"] = stat_block_web_path
                
                # 2. 更新数值 (Stats)
                if parsed_stats:
                    entity["stats"] = parsed_stats
                    entity["source"] = "custom_upload" # 标记来源
    
    if not updated:
        return {"warning": f"Enemy '{enemy_name}' not found in graph, but images saved if provided."}

    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(story_data, f, indent=2, ensure_ascii=False)

    return {
        "status": "success", 
        "image_path": avatar_web_path or stat_block_web_path,
        "stats_parsed": bool(parsed_stats)
    }


@router.post("/stories/{story_id}/characters/add")
async def add_character_to_story(
    story_id: str,
    # 1. 现有的 files 用来解析数据
    files: List[UploadFile] = File(...), 
    # 2. 新增 avatar 参数，可选，只用来存储
    avatar: Optional[UploadFile] = File(None), 
    background_info: Optional[str] = Form(None)
):
    story_folder = STORIES_DIR / story_id
    json_path = story_folder / "story.json"
    
    if not json_path.exists():
        raise HTTPException(404, "Story not found")

    # 准备目录
    char_sheet_folder = story_folder / "images" / "characters"
    avatar_folder = story_folder / "images" / "avatars"  # 新建一个 avatars 目录
    
    char_sheet_folder.mkdir(parents=True, exist_ok=True)
    avatar_folder.mkdir(parents=True, exist_ok=True)

    # --- A. 处理 Avatar (如果上传了的话) ---
    avatar_web_path = None
    if avatar:
        avatar_content = await avatar.read()
        ext = Path(avatar.filename).suffix
        # 给头像起个名，比如 avatar_uuid.png
        safe_avatar_name = f"avatar_{uuid.uuid4().hex[:6]}{ext}"
        avatar_save_path = avatar_folder / safe_avatar_name
        
        with open(avatar_save_path, "wb") as f:
            f.write(avatar_content)
        
        avatar_web_path = f"/static/data/stories/{story_id}/images/avatars/{safe_avatar_name}"

    # --- B. 处理人物卡 (用于 AI 解析) ---
    saved_file_paths = []
    image_contents = [] 

    for file in files:
        # 重置指针读取内容
        await file.seek(0)
        content = await file.read()
        image_contents.append(content)
        
        # 保存人物卡原图
        ext = Path(file.filename).suffix
        safe_filename = f"sheet_{uuid.uuid4().hex[:6]}_{file.filename.replace(' ', '_')}"
        if not safe_filename.endswith(ext): safe_filename += ext
            
        save_path = char_sheet_folder / safe_filename
        with open(save_path, "wb") as f:
            f.write(content)
            
        web_path = f"/static/data/stories/{story_id}/images/characters/{safe_filename}"
        saved_file_paths.append(web_path)

    # --- C. 调用 AI 解析 (只解析 files，不解析 avatar) ---
    try:
        char_data = parse_character_images(image_contents, user_context=background_info or "")
    except Exception as e:
        raise HTTPException(500, detail=f"AI Vision Failed: {str(e)}")
    
    # --- D. 更新 JSON ---
    with open(json_path, "r", encoding="utf-8") as f:
        story_data = json.load(f)
    
    if "characters" not in story_data:
        story_data["characters"] = []
        
    char_dict = char_data.model_dump() if hasattr(char_data, "model_dump") else char_data
    
    # 存入路径
    char_dict["file_path"] = saved_file_paths[0] 
    char_dict["all_files"] = saved_file_paths
    # 存入头像路径 (如果没有上传，就是 None)
    char_dict["avatar_path"] = avatar_web_path 
    
    story_data["characters"].append(char_dict)
    
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(story_data, f, indent=2, ensure_ascii=False)

    return {
        "status": "success", 
        "character_name": char_dict.get("name"),
        "avatar_url": avatar_web_path
    }

@router.post("/stories/{story_id}/scenes/{node_id}/upload-background")
async def upload_scene_background(
    story_id: str, 
    node_id: str,
    file: UploadFile = File(...)
):
    story_folder = STORIES_DIR / story_id
    json_path = story_folder / "story.json"
    
    if not json_path.exists():
        raise HTTPException(404, "Story not found")

    # 1. 准备目录
    bg_folder = story_folder / "images" / "backgrounds"
    bg_folder.mkdir(parents=True, exist_ok=True)

    # 2. 保存图片
    ext = Path(file.filename).suffix
    safe_filename = f"bg_{node_id}_{uuid.uuid4().hex[:6]}{ext}"
    save_path = bg_folder / safe_filename
    
    with open(save_path, "wb") as buffer:
        shutil.copyfileobj(file.file, buffer)

    # 3. 更新 JSON
    with open(json_path, "r", encoding="utf-8") as f:
        story_data = json.load(f)

    nodes = story_data.get("nodes", {})
    
    # 确保找到对应的节点
    if node_id not in nodes:
        return {"error": f"Node ID '{node_id}' not found in story."}
    
    # 生成 Web 路径
    web_path = f"/static/data/stories/{story_id}/images/backgrounds/{safe_filename}"
    
    # 更新节点的 image_path 字段
    nodes[node_id]["image_path"] = web_path
    
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(story_data, f, indent=2, ensure_ascii=False)

    return {"status": "success", "node_id": node_id, "image_path": web_path}


# ==========================================
# 4. 游戏会话接口 (Game Session)
# ==========================================

@router.post("/sessions/create", response_model=GameSession)
def create_new_session(request: SessionCreateRequest):
    try:
        return session_manager.create_session(request)
    except Exception as e:
        raise HTTPException(400, detail=str(e))

@router.get("/sessions")
def get_all_sessions():
    return session_manager.list_sessions()

@router.get("/sessions/{session_id}", response_model=GameSession)
def get_session(session_id: str):
    try:
        return session_manager.load_session(session_id)
    except FileNotFoundError:
        raise HTTPException(404, detail="Session not found")
    


@router.get("/sessions/{session_id}/render")
def get_session_render_data(session_id: str):
    """
    获取前端渲染所需的所有数据：包含完整的人物卡
    """
    try:
        session = session_manager.load_session(session_id)
    except FileNotFoundError:
        raise HTTPException(404, detail="Session not found")

    story_path = STORIES_DIR / session.story_id / "story.json"
    if not story_path.exists():
        raise HTTPException(404, detail="Story file missing")
        
    with open(story_path, "r", encoding="utf-8") as f:
        story_data = json.load(f)
    
    current_node = story_data["nodes"].get(session.current_node_id)
    if not current_node:
        return {"error": f"Node '{session.current_node_id}' not found."}

    player = session.players[0]
    
    return {
        "session_id": session.session_id,
        "character": {
            "name": player.name,
            "hp_current": player.current_hp,
            # 关键修改：直接把整个 character_sheet 传回去，让前端去解析细节
            "sheet": player.character_sheet.model_dump(by_alias=True), 
            "avatar": player.character_sheet.avatar_path or "https://placehold.co/100x100/333/ccc?text=Avatar"
        },
        "scene": {
            "title": current_node.get("title", "Unknown Location"),
            "image": current_node.get("image_path", ""), 
            "type": current_node.get("type", "transition")
        },
        "history": session.chat_history
    }

@router.post("/sessions/{session_id}/action", response_model=DMResponse)
def process_game_action(session_id: str, req: GameActionRequest):
    """正常推进剧情 (Action)"""
    try:
        return ai_dm.process_turn(session_id, req.action)
    except Exception as e:
        print(f"AI Error: {e}")
        raise HTTPException(500, detail=str(e))
    
# --- 新增：询问接口 (Query) ---
@router.post("/sessions/{session_id}/query", response_model=DMResponse)
def process_game_query(session_id: str, req: GameActionRequest):
    """
    玩家提问 (Query)
    特点：不推进时间，不触发转场，只回答问题 (Lore/Rules)
    """
    try:
        # 调用 ai_dm 的新方法 (稍后实现)
        return ai_dm.process_query(session_id, req.action)
    except Exception as e:
        print(f"AI Query Error: {e}")
        raise HTTPException(500, detail=str(e))
    
# 引入新 Agent
from app.engine.fight_agent import fight_agent

@router.post("/sessions/{session_id}/fight", response_model=DMResponse)
def process_fight_turn(session_id: str, req: GameActionRequest):
    """
    专属战斗接口：严谨的回合制处理
    """
    try:
        return fight_agent.process_fight_round(session_id, req.action)
    except Exception as e:
        print(f"Fight Error: {e}")
        raise HTTPException(500, detail=str(e))