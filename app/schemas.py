# app/schemas.py
from pydantic import BaseModel, Field
from typing import List, Optional, Dict, Any

# ==========================================
# 1. CHARACTER SHEET MODELS
# ==========================================

class AbilityScores(BaseModel):
    strength: int
    dexterity: int
    constitution: int
    intelligence: int
    wisdom: int
    charisma: int

class Attack(BaseModel):
    name: str = Field(..., description="Name of the weapon or attack")
    bonus: int = Field(..., description="Hit bonus")
    damage: str = Field(..., description="Damage dice string")
    damage_type: str = Field(..., description="Type of damage")

class Spellcasting(BaseModel):
    spell_save_dc: int
    spell_attack_bonus: int
    cantrips: List[str] = []
    level_1_spells: List[str] = []
    prepared_spells: List[str] = Field(..., description="List of currently prepared spells")

class CharacterSheet(BaseModel):
    # --- 关键修复：允许通过字段名 (class_name) 填充，而不仅是别名 (class) ---
    model_config = {"populate_by_name": True} 

    # Basic Info
    name: str = Field(..., description="Character's name")
    race: str
    # 这里定义了 alias="class"，导致了刚才的报错
    class_name: str = Field(..., alias="class", description="Class name")
    subclass: Optional[str] = None
    level: int = 1
    background: str
    alignment: str

    # Stats
    hp_max: int
    ac: int
    speed: int
    initiative: int
    proficiency_bonus: int

    # Abilities & Skills
    abilities: AbilityScores
    skill_proficiencies: List[str] = []
    saving_throw_proficiencies: List[str] = []

    # Combat & Magic
    attacks: List[Attack] = []
    spellcasting: Optional[Spellcasting] = None

    # Inventory & Features
    equipment: List[str] = []
    features: List[str] = []

    # Fluff
    background_story: str
    personality_traits: Optional[str] = None
    ideals: Optional[str] = None
    bonds: Optional[str] = None
    flaws: Optional[str] = None
    
    # Media Paths
    file_path: Optional[str] = None 
    all_files: List[str] = []       
    avatar_path: Optional[str] = None

# ==========================================
# 2. MONSTER SHEET MODELS
# ==========================================

class MonsterAction(BaseModel):
    name: str
    description: str
    attack_bonus: Optional[int] = None
    damage_dice: Optional[str] = None

class MonsterTrait(BaseModel):
    name: str
    description: str

class MonsterSheet(BaseModel):
    model_config = {"populate_by_name": True}

    name: str
    size: str
    type: str
    alignment: str
    
    ac: int
    ac_description: Optional[str] = None
    hp_max: int
    hp_formula: Optional[str] = None
    speed: str
    
    str: int
    dex: int
    con: int
    int: int
    wis: int
    cha: int
    
    skills: Optional[str] = None
    senses: Optional[str] = None
    languages: Optional[str] = None
    challenge_rating: str
    xp: Optional[int] = None
    
    traits: List[MonsterTrait] = []
    actions: List[MonsterAction] = []
    reactions: List[MonsterAction] = []
    legendary_actions: List[MonsterAction] = []

    file_path: Optional[str] = None

# ==========================================
# 3. STORY EDITOR MODELS
# ==========================================

class StoryCreateRequest(BaseModel):
    title: str
    raw_script: str

class StoryResponse(BaseModel):
    id: str
    title: str
    node_count: int
    file_path: str

# ==========================================
# 4. GAME RUNTIME SESSION MODELS
# ==========================================

class GameActionRequest(BaseModel):
    """前端发送给后端的玩家动作"""
    action: str

class DMResponse(BaseModel):
    """LLM 返回给系统的结构化指令"""
    narrative: str = Field(..., description="The story description to show the player.")
    damage_taken: int = Field(0, description="Amount of damage the player takes this turn (0 if none).")
    transition_to_id: Optional[str] = Field(None, description="The ID of the next node if the scene changes.")

class PlayerState(BaseModel):
    """运行时玩家状态 (动态)"""
    model_config = {"populate_by_name": True}

    name: str
    character_sheet: CharacterSheet # 静态数据的完整拷贝
    current_hp: int
    temp_hp: int = 0
    conditions: List[str] = []      
    inventory: List[str] = []       
    position: str = "default"       


class StoryNode(BaseModel):
    id: str
    title: str
    type: str # encounter, transition, roleplay
    read_aloud: str
    gm_guidance: str
    min_turns: int = Field(1, description="Minimum interaction turns required before transition") # <--- 新增这个
    environment: Dict[str, Any]
    entities: List[Dict[str, Any]]
    options: List[str]
    interactions: List[Dict[str, Any]]
    loot: List[Dict[str, Any]] = [] # <--- 新增物品列表
    edges: List[Dict[str, Any]]

class GameSession(BaseModel):
    """完整的游戏存档结构"""
    model_config = {"populate_by_name": True}

    session_id: str
    story_id: str
    title: str
    language: str = "en"
    
    # 进度指针
    current_node_id: str
    
    # --- 新增：节奏控制器 ---
    current_node_turns: int = 0  # 当前节点已经进行了多少轮对话
    # ----------------------
    
    # 状态
    players: List[PlayerState]
    enemy_states: Dict[str, Any] = {}
    
    chat_history: List[Dict[str, str]] = [] 
    
    created_at: str
    updated_at: str

class SessionCreateRequest(BaseModel):
    """创建新会话的请求参数"""
    story_id: str
    character_idx: int 
    player_name: str
    language: str = "en"

class DMResponse(BaseModel):
    """LLM 返回给系统的结构化指令"""
    narrative: str = Field(..., description="Story description")
    mechanics_log: Optional[str] = Field(None, description="Dice logs")
    damage_taken: int = Field(0, description="Damage to player")
    transition_to_id: Optional[str] = Field(None, description="Next scene node ID")
    
    # --- 新增字段 ---
    # 取值: "action" (探索模式) | "fight" (战斗模式) | null (保持当前)
    active_mode: Optional[str] = Field(None, description="Force frontend to switch tab.")