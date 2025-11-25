# app/engine/story.py
from __future__ import annotations

import json
from dataclasses import dataclass, field, asdict
from typing import Dict, List, Optional, Any

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
    """
    id: str
    title: str
    type: str = "encounter" 
    
    # --- 新增字段 ---
    min_turns: int = 1  # 默认 1 回合，防止 KeyError
    # ----------------
    
    read_aloud: str = ""
    gm_guidance: str = ""
    
    environment: EnvironmentSpec = field(default_factory=EnvironmentSpec)
    entities: List[EntitySpec] = field(default_factory=list)
    interactions: List[InteractionSpec] = field(default_factory=list)
    loot: List[LootSpec] = field(default_factory=list)
    
    edges: List[Edge] = field(default_factory=list)
    
    # 为了兼容之前的功能，建议加一个 image_path 字段
    image_path: Optional[str] = None


class StoryGraph:
    def __init__(self) -> None:
        self.nodes: Dict[str, SceneNode] = {}

    def add_scene(self, node: SceneNode) -> None:
        self.nodes[node.id] = node

    def add_scene_from_dict(self, data: Dict[str, Any]) -> SceneNode:
        scene_id = data["id"]
        
        env_data = data.get("environment", {})
        environment = EnvironmentSpec(
            light=env_data.get("light", "Normal"),
            terrain=env_data.get("terrain", "Normal"),
            sound=env_data.get("sound", ""),
            notes=env_data.get("notes")
        )

        entities = []
        for e in data.get("entities", []):
            entities.append(EntitySpec(
                name=e["name"],
                type=e.get("type", "monster"),
                ref_slug=e.get("ref_slug"),
                count=int(e.get("count", 1)),
                state=e.get("state", "Idle"),
                disposition=e.get("disposition", "hostile"),
                extra=e.get("extra", {})
            ))

        interactions = []
        for i in data.get("interactions", []):
            interactions.append(InteractionSpec(
                trigger=i["trigger"],
                mechanic=i.get("mechanic", "None"),
                success=i.get("success", ""),
                failure=i.get("failure")
            ))

        loot = []
        for l in data.get("loot", []):
            loot.append(LootSpec(
                item=l["item"],
                quantity=int(l.get("quantity", 1)),
                description=l.get("description")
            ))

        edges = []
        for ed in data.get("next", []):
            edges.append(Edge(
                to=ed["to"],
                weight=float(ed.get("weight", 1.0)),
                label=ed.get("label", ""),
                condition=ed.get("condition")
            ))

        node = SceneNode(
            id=scene_id,
            title=data.get("title", scene_id),
            type=data.get("type", "encounter"),
            
            # --- 关键修复：提取 min_turns ---
            min_turns=int(data.get("min_turns", 1)), 
            # ------------------------------
            
            read_aloud=data.get("read_aloud", ""),
            gm_guidance=data.get("gm_guidance", data.get("summary", "")),
            environment=environment,
            entities=entities,
            interactions=interactions,
            loot=loot,
            edges=edges,
            image_path=data.get("image_path") # 支持从 JSON 加载图片路径
        )
        self.add_scene(node)
        return node

    def add_scenes_from_json_list(self, json_str: str) -> List[SceneNode]:
        try:
            data = json.loads(json_str)
        except json.JSONDecodeError as e:
            raise ValueError(f"Invalid JSON string: {e}")

        if isinstance(data, dict):
            data = [data]
        
        if not isinstance(data, list):
             raise ValueError("Input JSON must be a list of scene objects.")

        created_nodes = []
        for scene_data in data:
            node = self.add_scene_from_dict(scene_data)
            created_nodes.append(node)
        
        return created_nodes

    def to_dict(self) -> Dict[str, Any]:
        out_nodes = {}
        for sid, node in self.nodes.items():
            node_dict = asdict(node)
            out_nodes[sid] = node_dict
        return {"nodes": out_nodes}

    def to_json(self, indent: int = 2) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, indent=indent)