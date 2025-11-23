# aidnd_story_graph.py
"""
Story graph tools for AI DnD.

This module defines a comprehensive directed graph structure for representing 
a DnD adventure as a playable state machine.

Each node is a "Scene" containing:
  - Narrative text (Boxed text vs GM secrets)
  - Environment state (Light, Terrain)
  - Entities (Monsters, NPCs) with states
  - Interactive Mechanics (Triggers, DC checks, Outcomes)
  - Loot
  - Transitions (Edges with conditions)

The LLM is expected to output a JSON List of these scenes.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field, asdict
from typing import Dict, List, Optional, Any, Tuple


# ---------------------------------------------------------------------------
# Core data structures
# ---------------------------------------------------------------------------

@dataclass
class EnvironmentSpec:
    """
    Physical characteristics of the scene affecting gameplay.
    """
    light: str = "Bright light"
    terrain: str = "Normal"
    sound: str = "Quiet"
    notes: Optional[str] = None

@dataclass
class EntitySpec:
    """
    An active entity in the scene (Monster, NPC, or sentient object).
    """
    name: str
    type: str = "monster"  # monster, npc, object
    ref_slug: Optional[str] = None
    count: int = 1
    state: str = "Idle"    # e.g., "Hiding", "Patrolling", "Sleeping"
    disposition: str = "hostile" # hostile, neutral, friendly
    extra: Dict[str, Any] = field(default_factory=dict)

@dataclass
class InteractionSpec:
    """
    A specific mechanic or check available in the scene.
    e.g., "Investigate the altar" -> "DC 15 Int" -> "Find hidden switch"
    """
    trigger: str
    mechanic: str          # e.g., "DC 15 Intelligence (Investigation)"
    success: str
    failure: Optional[str] = None

@dataclass
class LootSpec:
    """
    Rewards found in the scene.
    """
    item: str
    quantity: int = 1
    description: Optional[str] = None

@dataclass
class Edge:
    """
    Directed edge to another scene.
    """
    to: str
    weight: float = 1.0
    label: str = ""
    condition: Optional[str] = None  # e.g., "Requires Iron Key" or "If Goblins defeated"

@dataclass
class SceneNode:
    """
    A single game state/location in the adventure.
    """
    id: str
    title: str
    type: str = "encounter" # encounter, roleplay, exploration, puzzle, transition
    
    # Text Content
    read_aloud: str = ""    # Flavour text to read to players
    gm_guidance: str = ""   # Secrets, plot context, or summary for the DM
    
    # Game State
    environment: EnvironmentSpec = field(default_factory=EnvironmentSpec)
    entities: List[EntitySpec] = field(default_factory=list)
    interactions: List[InteractionSpec] = field(default_factory=list)
    loot: List[LootSpec] = field(default_factory=list)
    
    # Navigation
    edges: List[Edge] = field(default_factory=list)


class StoryGraph:
    """
    Directed graph of scenes for a DnD adventure.
    """

    def __init__(self) -> None:
        self.nodes: Dict[str, SceneNode] = {}

    # ---------------------- Node / edge construction ----------------------

    def add_scene(self, node: SceneNode) -> None:
        """
        Insert or replace a scene node in the graph.
        """
        self.nodes[node.id] = node

    def add_scene_from_dict(self, data: Dict[str, Any]) -> SceneNode:
        """
        Build a SceneNode from a Python dict following the new comprehensive schema.
        Robustly handles missing optional fields by using defaults.
        """
        scene_id = data["id"]
        
        # 1. Parse Environment
        env_data = data.get("environment", {})
        environment = EnvironmentSpec(
            light=env_data.get("light", "Normal"),
            terrain=env_data.get("terrain", "Normal"),
            sound=env_data.get("sound", ""),
            notes=env_data.get("notes")
        )

        # 2. Parse Entities
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

        # 3. Parse Interactions
        interactions = []
        for i in data.get("interactions", []):
            interactions.append(InteractionSpec(
                trigger=i["trigger"],
                mechanic=i.get("mechanic", "None"),
                success=i.get("success", ""),
                failure=i.get("failure")
            ))

        # 4. Parse Loot
        loot = []
        for l in data.get("loot", []):
            loot.append(LootSpec(
                item=l["item"],
                quantity=int(l.get("quantity", 1)),
                description=l.get("description")
            ))

        # 5. Parse Edges (Next)
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
            read_aloud=data.get("read_aloud", ""),
            gm_guidance=data.get("gm_guidance", data.get("summary", "")), # Fallback to summary if provided
            environment=environment,
            entities=entities,
            interactions=interactions,
            loot=loot,
            edges=edges,
        )
        self.add_scene(node)
        return node

    def add_scenes_from_json_list(self, json_str: str) -> List[SceneNode]:
        """
        Parse a JSON string containing a LIST of scene objects.
        This is the preferred method for bulk LLM ingestion.
        """
        try:
            data = json.loads(json_str)
        except json.JSONDecodeError as e:
            raise ValueError(f"Invalid JSON string: {e}")

        if isinstance(data, dict):
            # Handle case where LLM outputs a single object instead of a list
            data = [data]
        
        if not isinstance(data, list):
             raise ValueError("Input JSON must be a list of scene objects.")

        created_nodes = []
        for scene_data in data:
            node = self.add_scene_from_dict(scene_data)
            created_nodes.append(node)
        
        return created_nodes

    def add_edge(self, from_id: str, to_id: str, weight: float = 1.0, label: str = "", condition: str = None) -> None:
        """
        Add a directed edge between two existing scenes manually.
        """
        if from_id not in self.nodes:
            raise KeyError(f"from_id not found in graph: {from_id}")
        edge = Edge(to=to_id, weight=weight, label=label, condition=condition)
        self.nodes[from_id].edges.append(edge)

    # ---------------------- Validation & Introspection ------------------------

    def get_scene(self, scene_id: str) -> Optional[SceneNode]:
        return self.nodes.get(scene_id)

    def list_scene_ids(self) -> List[str]:
        return list(self.nodes.keys())

    def validate_graph(self) -> List[str]:
        """
        Checks for consistency issues.
        Returns a list of warning strings (e.g., dangling edges).
        """
        warnings = []
        for sid, node in self.nodes.items():
            for edge in node.edges:
                if edge.to not in self.nodes:
                    warnings.append(f"Dangling edge in '{sid}': points to non-existent '{edge.to}'")
        return warnings

    def check_is_dag(self) -> bool:
        """
        Return True if the graph has no directed cycles.
        Note: D&D adventures often HAVE cycles (backtracking), so allow False.
        """
        visited: Dict[str, int] = {}  # 0=new, 1=visiting, 2=visited

        def dfs(u: str) -> bool:
            state = visited.get(u, 0)
            if state == 1: return False # Cycle
            if state == 2: return True
            
            visited[u] = 1
            node = self.nodes.get(u)
            if node:
                for e in node.edges:
                    if e.to in self.nodes:
                        if not dfs(e.to): return False
            visited[u] = 2
            return True

        for sid in self.nodes:
            if visited.get(sid, 0) == 0:
                if not dfs(sid): return False
        return True

    # ---------------------- Visualization ------------------------

    def to_mermaid(self) -> str:
        """
        Generates a Mermaid JS flowchart string from the graph.
        Great for debugging or showing the map to the DM.
        """
        lines = ["graph TD"]
        for node in self.nodes.values():
            # Escape quotes for Mermaid safety
            safe_title = node.title.replace('"', "'")
            
            # Different shapes based on type? 
            # ( brackets ) = round edges, [ brackets ] = sharp edges
            if node.type == 'combat':
                shape_open, shape_close = "([", "])" # Stadium shape for combat
            elif node.type == 'transition':
                shape_open, shape_close = "{{", "}}" # Hexagon for transition
            else:
                shape_open, shape_close = "[", "]"
            
            lines.append(f'    {node.id}{shape_open}"{safe_title}"{shape_close}')
            
            for edge in node.edges:
                label = edge.label[:20] + "..." if len(edge.label) > 20 else edge.label
                if edge.condition:
                    label += f" ({edge.condition})"
                
                # A -- Label --> B
                if label:
                    lines.append(f'    {node.id} -- "{label}" --> {edge.to}')
                else:
                    lines.append(f'    {node.id} --> {edge.to}')
        
        return "\n".join(lines)

    # ---------------------- Serialization ------------------------

    def to_dict(self) -> Dict[str, Any]:
        """
        Export the whole graph as a JSON-serializable dict.
        Uses dataclasses.asdict for nested objects.
        """
        out_nodes = {}
        for sid, node in self.nodes.items():
            # Convert dataclass to dict, then clean up if necessary
            node_dict = asdict(node)
            # asdict is recursive, so entities, edges, etc are already dicts
            out_nodes[sid] = node_dict
        return {"nodes": out_nodes}

    def to_json(self, indent: int = 2) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, indent=indent)