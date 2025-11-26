# app/engine/story.py
from __future__ import annotations

import json
from dataclasses import dataclass, field, asdict
from typing import Dict, List, Optional, Any

# ✅ 引入 Pydantic 的 StoryNode schema
# 路径不同时，把这一行改成实际路径即可
from app.schemas import StoryNode


# ---------------------------------------------------------------------------
# Core data structures
# ---------------------------------------------------------------------------

@dataclass
class EnvironmentSpec:
    light: str = "Bright light"
    terrain: str = "Normal"
    sound: str = "Quiet"
    notes: Optional[str] = None


@dataclass
class EntitySpec:
    name: str
    type: str = "monster"
    ref_slug: Optional[str] = None
    count: int = 1
    state: str = "Idle"
    disposition: str = "hostile"
    extra: Dict[str, Any] = field(default_factory=dict)


@dataclass
class InteractionSpec:
    trigger: str
    mechanic: str
    success: str
    failure: Optional[str] = None


@dataclass
class LootSpec:
    item: str
    quantity: int = 1
    description: Optional[str] = None


@dataclass
class Edge:
    to: str
    weight: float = 1.0
    label: str = ""
    condition: Optional[str] = None


@dataclass
class SceneNode:
    """
    A single game state/location in the adventure.
    Internal runtime representation used by the engine.
    """
    id: str
    title: str
    type: str = "encounter"  # encounter, transition, roleplay

    # --- 新增字段：支持最小回合数 ---
    min_turns: int = 1

    read_aloud: str = ""
    gm_guidance: str = ""

    environment: EnvironmentSpec = field(default_factory=EnvironmentSpec)
    entities: List[EntitySpec] = field(default_factory=list)
    interactions: List[InteractionSpec] = field(default_factory=list)
    loot: List[LootSpec] = field(default_factory=list)

    # ✅ 新增：把 options 也保存下来，方便前端展示按钮
    # schema 那边可以是 List[Any] 或 List[Union[str, Dict[str, Any]]]
    options: List[Any] = field(default_factory=list)

    edges: List[Edge] = field(default_factory=list)

    # 为了兼容之前的功能，建议加一个 image_path 字段
    image_path: Optional[str] = None


class StoryGraph:
    def __init__(self) -> None:
        self.nodes: Dict[str, SceneNode] = {}

    def add_scene(self, node: SceneNode) -> None:
        self.nodes[node.id] = node

    # -----------------------------------------------------------------------
    # ✅ 核心改动：先用 StoryNode schema 做解析 + 校验
    # -----------------------------------------------------------------------
    def add_scene_from_dict(self, data: Dict[str, Any]) -> SceneNode:
        """
        从原始 dict（例如 LLM 输出 / JSON 反序列化）里读取一个节点，
        先用 Pydantic 的 StoryNode 进行校验和默认值填充，
        然后再转换为内部的 SceneNode。
        """
        # 1. 用 StoryNode 做一次标准化和类型校验
        story_node = StoryNode(**data)

        # 2. Environment
        env_data = story_node.environment or {}
        environment = EnvironmentSpec(
            light=env_data.get("light", "Normal"),
            terrain=env_data.get("terrain", "Normal"),
            sound=env_data.get("sound", ""),
            notes=env_data.get("notes"),
        )

        # 3. Entities
        entities: List[EntitySpec] = []
        for e in story_node.entities or []:
            entities.append(
                EntitySpec(
                    name=e["name"],
                    type=e.get("type", "monster"),
                    ref_slug=e.get("ref_slug"),
                    count=int(e.get("count", 1)),
                    state=e.get("state", "Idle"),
                    disposition=e.get("disposition", "hostile"),
                    extra=e.get("extra", {}),
                )
            )

        # 4. Interactions
        interactions: List[InteractionSpec] = []
        for i in story_node.interactions or []:
            interactions.append(
                InteractionSpec(
                    trigger=i["trigger"],
                    mechanic=i.get("mechanic", "None"),
                    success=i.get("success", ""),
                    failure=i.get("failure"),
                )
            )

        # 5. Loot（schema 里没有的话就从原始 data 兜底）
        loot: List[LootSpec] = []
        for l in data.get("loot", []):
            loot.append(
                LootSpec(
                    item=l["item"],
                    quantity=int(l.get("quantity", 1)),
                    description=l.get("description"),
                )
            )

        # 6. Edges ✅ 这里改为 story_node.edges（JSON 字段叫 edges，不是 next）
        edges: List[Edge] = []
        for ed in story_node.edges or []:
            edges.append(
                Edge(
                    to=ed["to"],
                    weight=float(ed.get("weight", 1.0)),
                    label=ed.get("label", ""),
                    condition=ed.get("condition"),
                )
            )

        # 7. Options：直接挂在 SceneNode 上，供前端使用
        #   - 如果 LLM 生成的是简单字符串数组，也可以先做一层清洗
        raw_options = story_node.options or []
        # 这里直接塞进 SceneNode，保持灵活性
        options: List[Any] = raw_options

        # 8. 构造内部 SceneNode
        node = SceneNode(
            id=story_node.id,
            title=story_node.title or story_node.id,
            type=story_node.type or "encounter",
            min_turns=int(story_node.min_turns or 1),
            read_aloud=story_node.read_aloud or "",
            gm_guidance=story_node.gm_guidance or "",
            environment=environment,
            entities=entities,
            interactions=interactions,
            loot=loot,
            options=options,
            edges=edges,
            image_path=data.get("image_path"),  # 仍然支持从 JSON 加载图片路径
        )

        self.add_scene(node)
        return node

    # 如果你有地方已经拿到了 StoryNode 实例，也可以用这个 helper
    def add_scene_from_story_node(self, story_node: StoryNode) -> SceneNode:
        """
        直接从 StoryNode（Pydantic 实例）构建 SceneNode。
        和 add_scene_from_dict 内部逻辑保持一致。
        """
        return self.add_scene_from_dict(story_node.model_dump())

    # -----------------------------------------------------------------------
    # 批量加载 / 导出
    # -----------------------------------------------------------------------
    def add_scenes_from_json_list(self, json_str: str) -> List[SceneNode]:
        """
        输入是一个 JSON 字符串：
        - 要么是单个对象 { ... }
        - 要么是对象数组 [ {...}, {...}, ... ]
        """
        try:
            data = json.loads(json_str)
        except json.JSONDecodeError as e:
            raise ValueError(f"Invalid JSON string: {e}")

        if isinstance(data, dict):
            data = [data]

        if not isinstance(data, list):
            raise ValueError("Input JSON must be a list of scene objects.")

        created_nodes: List[SceneNode] = []
        for scene_data in data:
            node = self.add_scene_from_dict(scene_data)
            created_nodes.append(node)

        return created_nodes

    def to_dict(self) -> Dict[str, Any]:
        """
        导出当前图为可 JSON 化的 dict。
        注意这里只输出 nodes，外层的 story id/title/characters
        一般在别的地方（比如数据库模型或 API schema）包一层。
        """
        out_nodes: Dict[str, Any] = {}
        for sid, node in self.nodes.items():
            node_dict = asdict(node)
            out_nodes[sid] = node_dict
        return {"nodes": out_nodes}

    def to_json(self, indent: int = 2) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, indent=indent)
