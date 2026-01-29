# app/engine/fight_agent.py
import os
from typing import Optional

# Import LangGraph Workflow
from app.engine.agents.combat import combat_graph
from app.schemas import DMResponse
from app.engine.session import session_manager

class FightAgent:
    def process_fight_round(self, session_id: str, player_input: str) -> DMResponse:
        """
        Fight Agent 主逻辑 (Modern Agent Architecture):
        - 使用 LangGraph (combat_graph) 进行 Plan-Simulator-Narrator 循环。
        """
        print(f"⚔️ [LangGraph] Invoking Combat Agent for session {session_id}")
        
        # Invoke Graph
        try:
            result = combat_graph.invoke({
                "session_id": session_id,
                "player_input": player_input
            })
        except Exception as e:
            # Fallback for error handling
            print(f"❌ [Combat Agent Error] {e}")
            import traceback
            traceback.print_exc()
            return DMResponse(
                narrative="System Error: The combat agent encountered an issue.",
                damage_taken=0,
                active_mode="fight"
            )
        
        # Extract Results
        # Note: HP updates and history appending are handled INSIDE the graph (update_session node)
        # We just need to return the response to the API.
        
        # Wait, if update_session handles history, we shouldn't do it again here?
        # The API endpoint usually expects DMResponse to return the text, 
        # but the Session object is the source of truth for history.
        # The frontend likely displays the Chat History OR the current response.
        # Usually frontend appends the response locally too. 
        # Let's ensure we return the same data.
        
        return DMResponse(
            narrative=result.get("final_narrative", ""),
            mechanics_log=result.get("mechanics_log"),
            active_mode=result.get("active_mode", "fight"),
            damage_taken=0 # Graph handled HP directly
        )

fight_agent = FightAgent()
