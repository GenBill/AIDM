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
Break the text into a **JSON List** of Scene Nodes. Do not create duplicated same node and do not create redundent node!! Important: YOU MUST create a directed acyclic graph, which means you cannot backtrack. Create a new node whenever:
* The location changes.
* A specific encounter (Combat/Social) begins.
* The narrative "chapter" shifts.
Important: YOU MUST create a directed acyclic graph!

## 2. Field Extraction Guidelines
* **id**: Snake_case unique identifier (e.g., `merrow_encounter`).
* **type**: `encounter`, `roleplay`, `transition`, `combat`, `puzzle` or `destination`.

- "roleplay":
  Pure social or narrative scenes. Focus on dialogue, characterization, and free-form description. Do NOT start initiative or detailed combat here, even if tensions are high. You may foreshadow danger, but keep it conversational.

- "transition":
  Short connective scenes that move the party from one situation or location to the next (travel, time skips, scene wrap-ups). Keep these brief and focused on pacing and mood. Usually there is no complex rules interaction here.

- "encounter":
  A structured scene with a clear situation or opposition (monsters, hazards, NPCs, dilemmas), but combat is NOT guaranteed.
  - In an encounter node, you describe the situation and then ask what the players do.
  - The players might negotiate, trick the enemy, retreat, or use creative tactics.
  - Combat MAY happen, but only if the players choose to attack or clearly escalate the conflict.
  - If the story graph has a child node of type "combat", you should only move into that combat node when the fiction clearly supports starting a fight (e.g., a character explicitly attacks, or talks clearly break down).

- "combat":
  Turn-based D&D combat is happening right now.
  - Treat this as a signal that initiative has already been rolled or must be rolled immediately.
  - The focus is on rounds, turns, positions, and actions, not long narrative digressions.
  - In our system, "combat" nodes are handled by the dedicated Combat Agent and the frontend should switch into the fight UI.
  - When you move the story into a node of type "combat", you should also signal that combat mode is now active so that control can pass to the Combat Agent.

- "puzzle":
  A scene centered on solving a riddle, trap, or puzzle-like situation.
  - Emphasize clues, player reasoning, and step-by-step attempts.
  - Avoid skipping straight to the solution unless the players clearly figure it out or fail repeatedly.

- "destination":
  The endpoint of the whole adventure.
  - This node type signifies the conclusion of the story.
  - This node should have no outgoing edges.
  - Provide a satisfying wrap-up, epilogue, or summary of outcomes.

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

* **options**:
    * A flat list of high-level choices that are **shown to the player** as buttons or menu options.
    * Example: `["Negotiate", "Combat", "Creative Trick"]`.
    * Every string in `options` MUST **exactly match** the `trigger` field of one object in `interactions`.
    * The engine will use this 1-to-1 mapping: when the player chooses an option, it finds the `interaction` with the same `trigger` and uses its `mechanic` / `success` / `failure`.

* **interactions**:
    * Convert mechanics into structured objects.
    * For **each** entry in `options`, create **one** corresponding `interaction` whose `trigger` is exactly the same text.
    * Format:  
      `{ "trigger": "Negotiate", "mechanic": "DC 15 Charisma", "success": "Cost reduced by 100gp", "failure": "Merrow gets angry and combat starts" }`
    * Do **not** create extra `interaction.trigger` values that are not present in `options` (except for rare special cases you are explicitly instructed to add).


* **edges**: List of transitions.

# Output Format
Return **ONLY** a valid JSON List. Do not wrap in markdown blocks if possible.
Here is an example structure:
[
"merrow_appears_on_deck": {
  "id": "merrow_appears_on_deck",
  "title": "The Merrow Boards the Ship",
  "type": "encounter",
  "min_turns": 4,
  "read_aloud": "As lightning flashes across the sky, a monster hauls itself up onto the deck!\n\n“These waters belong to the Scaled Queen. I’m here to collect her tribute.”",
  "gm_guidance": "Show the illustration of the merrow and adopt an intimidating voice when speaking its lines. Explain to the players that they can choose how to respond: fight, negotiate, trick, or attempt other creative tactics. Explicitly tell them that almost anything they try will involve rolling a d20 and adding a number from their sheet. Let the players take the lead and describe their ideas; encourage them to say what they are thinking and then translate that into actions and checks.\nUse vivid descriptions of the storm, the wet deck, and the looming merrow to keep tension high regardless of approach. This scene can become combat, negotiation, or a mix. The merrow is here to extort tribute for the Scaled Queen, not to immediately slaughter everyone, so it is willing to talk and threaten before fighting.\nIf the players ask about the Scaled Queen, have the merrow boast that she is a huge, two-headed merrow blessed by Demogorgon, the Prince of Demons. Use this to seed future fear and lore.\nIf the characters attack, transition into structured combat (see next scene). If they negotiate or use tricks, resolve via ability checks here. The encounter should be winnable and not overly lethal; the merrow is fearsome but mechanically modest.",
  "environment": {
    "light": "Dim",
    "terrain": "Difficult (Wet ship deck)",
    "sound": "Thunder, crashing waves, wind whipping sails, creaking rigging, merrow snarls",
    "notes": null
  },
  "entities": [
    {
      "name": "Merrow Extortionist",
      "type": "monster",
      "ref_slug": "merrow_extortionist",
      "count": 1,
      "state": "Hauling itself onto the deck, looming over the characters, demanding tribute",
      "disposition": "hostile",
      "extra": {}
    },
    {
      "name": "Ship Crew",
      "type": "npc",
      "ref_slug": "ship_crew_generic",
      "count": 4,
      "state": "Nervous and watching, ready to follow the characters’ lead if rallied",
      "disposition": "neutral",
      "extra": {}
    }
  ],

  "options": [
    "Negotiate",
    "Combat",
    "Creative Tactic",
    "Ask about the Scaled Queen"
  ],

  "interactions": [
    {
      "trigger": "Negotiate",
      "mechanic": "DC 15 Charisma (Persuasion, Intimidation, or Deception check, depending on approach)",
      "success": "The merrow agrees to reduced tribute or accepts an alternative bargain. Tension remains high but full combat may be avoided.",
      "failure": "The merrow grows impatient and hostile. It demands full tribute and is ready to attack, moving toward the combat scene."
    },
    {
      "trigger": "Combat",
      "mechanic": "No check required. Players declare attacks and initiative is rolled.",
      "success": "Structured combat begins against the merrow (transition to the merrow combat node).",
      "failure": "N/A – once combat is chosen, the scene must enter structured combat."
    },
    {
      "trigger": "Creative Tactic",
      "mechanic": "One or more ability checks (e.g. Athletics, Acrobatics, Performance, or others) typically DC 13–15, depending on the specific plan.",
      "success": "The characters’ unusual tactic (rolling barrels, dropping sails, rallying crew, etc.) creates a strong advantage or bypasses tribute on clever terms, usually leading to the creative-resolution node.",
      "failure": "The plan backfires or only partially works. The merrow becomes angry or feels mocked, and the situation is likely to escalate into combat."
    },
    {
      "trigger": "Ask about the Scaled Queen",
      "mechanic": "No check required.",
      "success": "The merrow explains that the Scaled Queen is a huge, two-headed merrow blessed by Demogorgon, the Prince of Demons, seeding future fear and lore.",
      "failure": "N/A – the merrow is eager to boast about its mistress and will share this information freely."
    }
  ],
  "edges": [
    {
      "to": "merrow_negotiation",
      "weight": 1.0,
      "label": "Characters attempt to negotiate or pay tribute",
      "condition": "Players choose to talk, bargain, or intimidate rather than immediately attack"
    },
    {
      "to": "merrow_combat",
      "weight": 1.0,
      "label": "Characters attack the merrow",
      "condition": "Any character declares an attack or hostilities break out"
    },
    {
      "to": "merrow_shenanigans",
      "weight": 1.0,
      "label": "Characters attempt a creative nonstandard tactic",
      "condition": "Players propose unusual tactics like rolling barrels, dropping sails, or rallying crew"
    }
  ]
},

]

# Important Note:
At the end of the story you MUST create a final scene node with type="destination". 
This node must contain an epilogue or closing summary. 
This node must have no outgoing edges.

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
            model="gpt-5.1",
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": f"Please process the following adventure text:\n\n{raw_text}"}
            ],
            temperature=0.1, # 保持低温以稳定输出
        )
        llm_output = response.choices[0].message.content
            # A. 先打印原始 LLM 输出
        print("=== RAW LLM OUTPUT ===")
        print(llm_output)

        #json_str = clean_json_text(llm_output)
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