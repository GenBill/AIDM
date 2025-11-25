# app/services/story_generator.py
import json
import os
import re
from openai import OpenAI
from app.engine.story import StoryGraph # 引用你的核心类

client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))

# ======================================================
# 1. 继承自 test_story_gen.py 的 SYSTEM_PROMPT
# ======================================================
SYSTEM_PROMPT = """
# Role
You are an expert AI Dungeon Master and Data Architect. Your task is to convert unstructured D&D adventure text into a fully structured, playable JSON Graph.

# Core Directive: Inference & Completion
The input text may be narrative and lack explicit structured data. **You must infer missing details based on context.**
1. **Environment**: If the text says "stormy", infer `sound="Thunder"`, `light="Dim"`.
2. **Mechanics**: Create `InteractionSpec` for challenges even if specific DCs aren't listed (estimate based on difficulty).
3. **Transitions**: Invent logical IDs if explicit scene transitions aren't named.

# Data Structure Rules

## 1. Scene Segmentation
Break the text into a **JSON List** of Scene Nodes. Do not create duplicated same node!! Important: YOU MUST create a directed acyclic graph, which means you cannot backtrack. Create a new node whenever:
* The location changes.
* A specific encounter (Combat/Social) begins.
* The narrative "chapter" shifts.
Important: YOU MUST create a directed acyclic graph!

## 2. Field Extraction Guidelines
* **id**: Snake_case unique identifier (e.g., `merrow_encounter`).
* **type**: `encounter` (default), `roleplay`, `transition`, or `puzzle`.

* **min_turns** (Complexity Score): **CRITICAL**. Analyze the content to determine how many turns (interactions) players should spend here before the system suggests moving on.
    * **Use the following RUBRIC to assign `min_turns`**:
    * **2 Turn (Simple)**: Pure transition scenes, empty rooms, or simple observations. (e.g., "You walk down the hallway.")
    * **3 Turns (Standard)**: Minor interactions, investigating a room with loot, or talking to a simple NPC.
    * **4 Turns (Complex)**: Standard combat encounters (e.g., 3 Zombies), puzzles with 1-2 steps, or important NPC negotiations.
    * **6-7 Turns (Major)**: Boss fights, complex multi-stage puzzles, or major lore dumps requiring multiple questions.

* **read_aloud**: Extract text explicitly meant to be read to players (often in boxes or quotes).
    * *Constraint*: Do NOT put rules, secrets, or enemy stats here.

* **gm_guidance**: **CRITICAL FIELD**. This acts as the "Brain" for the runtime AI.
    * **DO NOT SUMMARIZE**. Extract all detailed instructions.
    * **Include Context**: Specific details about the setting (e.g., "Blood-red sunrise").
    * **Include Logic Branches**: Explicitly state "If X happens, then Y". (e.g., "If players lose, Elder Runara rescues them.").
    * **Include Tactics**: How enemies behave or negotiate (e.g., "Demands 400gp, DC 15 check reduces by 100gp").
    * **Include Tips**: "Shenanigans" or creative solutions mentioned in the text.
    * **MANDATORY**: Extract all "If/Then" logic, especially:
        * **Success Outcomes**: What happens if they win?
        * **Failure Outcomes**: (CRITICAL) What happens if they lose? (e.g. "Runara rescues them").
        * **Knowledge Checks**: Any "DC X Intelligence check to know Y".
* **environment**:
    * `light`: "Bright", "Dim", "Darkness".
    * `terrain`: "Normal", "Difficult (Sand)", "Water".
    * `sound`: Inferred ambient sounds.

* **entities**:
    * Extract explicit enemies/NPCs.
    * Only keep `name`, `count`, and generate a `ref_slug`.
    * Infer `disposition`: "hostile", "friendly", "neutral".
    * `state`: Initial position or activity (e.g., "Emerging from water").

* **interactions**:
    * Convert mechanics into structured objects.
    * Format: `{ "trigger": "Negotiate", "mechanic": "DC 15 Charisma", "success": "Cost reduced by 100gp", "failure": "Merrow gets angry" }`

* **loot**: List items or [].
* **next**: List of transitions.

# Output Format
Return **ONLY** a valid JSON List. Do not wrap in markdown blocks if possible.
Here is an example structure:
[
  {
    "id": "unique_scene_id",
    "title": "Scene Title",
    "type": "encounter",
    "min_turns": 4,
    "read_aloud": "Flavor text...",
    "gm_guidance": "DM secrets...",
    "environment": {
      "light": "Inferred Light",
      "terrain": "Inferred Terrain",
      "sound": "Inferred Sound"
    },
    "entities": [
      {
        "name": "Name",
        "type": "monster",
        "ref_slug": "slug",
        "count": 1,
        "state": "Initial state",
        "disposition": "hostile"
      }
    ],
    "interactions": [
      {
        "trigger": "Action",
        "mechanic": "DC X Check",
        "success": "Result"
      }
    ],
    "loot": [],
    "next": [
      {
        "to": "next_scene_id",
        "label": "Transition trigger",
        "condition": "Optional condition"
      }
    ]
  }
]
"""

# ======================================================
# 2. 继承自 test_story_gen.py 的 Helper Function
# ======================================================
def clean_json_text(text: str) -> str:
    """去除 markdown 符号，防止解析报错"""
    pattern = r"```(?:json)?\s*([\s\S]*?)\s*```"
    match = re.search(pattern, text)
    if match:
        return match.group(1)
    return text.strip()

# ======================================================
# 3. 核心生成逻辑 (改造适配 API)
# ======================================================
def generate_story_from_text(raw_text: str) -> dict:
    """
    接收文本 -> LLM -> 清洗 -> StoryGraph 校验 -> 返回 Dict
    """
    try:
        # A. 调用 LLM
        response = client.chat.completions.create(
            model="gpt-4o",
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": f"Please process the following adventure text:\n\n{raw_text}"}
            ],
            temperature=0.1, # 保持低温以稳定输出
        )
        llm_output = response.choices[0].message.content
        
        # B. 清洗数据
        json_str = clean_json_text(llm_output)
        
        # C. 关键步骤：使用 StoryGraph 类进行“试加载”
        # 这能确保生成的 JSON 格式绝对符合我们的引擎要求，
        # 如果不符合，这里会直接抛错，避免保存了坏文件。
        temp_graph = StoryGraph()
        temp_graph.add_scenes_from_json_list(json_str)
        
        # D. 导出为纯 Dict (准备存入 json 文件)
        # 我们利用 to_dict() 方法，而不是直接用 llm 的原始 json
        story_data = temp_graph.to_dict()
        
        # E. 补全根级别的字段 (因为 StoryGraph 只管 nodes)
        # 我们需要手动在外层包一个结构，或者让 to_dict() 包含它
        # 这里我们假设 StoryGraph.to_dict() 返回的是 {"nodes": {...}}
        
        return story_data

    except Exception as e:
        print(f"Error inside generate_story_from_text: {e}")
        # 在实际 API 中，最好抛出 HTTPException，这里先返回 None 让 Route 处理
        raise e