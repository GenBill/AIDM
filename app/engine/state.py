from typing import TypedDict, Annotated, List, Optional, Any
from langgraph.graph.message import add_messages
from langchain_core.messages import BaseMessage

class NarrativeAgentState(TypedDict):
    """LangGraph State for Narrative DM"""
    messages: Annotated[List[BaseMessage], add_messages]
    
    # Inputs
    session_id: str
    player_input: str
    
    # Context (Loaded from DB/Files)
    system_prompt: str
    player_name: str
    player_hp: int
    current_node: dict
    story_context_text: str
    
    # Internal Processing
    mechanics_logs: List[str]
    
    # Final Outputs
    final_narrative: str
    transition_to_id: Optional[str]
    active_mode: Optional[str]

class CombatAgentState(TypedDict):
    """LangGraph State for Fight Agent"""
    messages: Annotated[List[BaseMessage], add_messages]
    
    # Inputs
    session_id: str
    player_input: str
    language: str
    
    # Context
    player_data: dict
    enemy_data: dict
    last_dm_narration: str
    
    # Planner Output
    combat_plan: dict
    
    # Simulation Output
    round_summary: dict
    
    # Final Output
    final_narrative: str
    mechanics_log: str
    active_mode: str
