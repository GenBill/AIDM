# test_story_gen.py
"""
Story Generation Workflow / Test Script.

This script tests the LLM's ability to extract a structured StoryGraph
from raw D&D module text.

It uses the 'Dungeon Architect' system prompt to enforce:
- Inference of environment details (Light, Sound)
- Extraction of logic (Mechanics, Interactions)
- Creation of a coherent scene list

Usage:
  export OPENAI_API_KEY="sk-..."
  python test_story_gen.py
"""

import os
import json
import re
import logging
from datetime import datetime
from openai import OpenAI

# Import your graph definition
from app.engine.story import StoryGraph

# ================== System Prompt (The Dungeon Architect) ==================
SYSTEM_PROMPT = """
# Role
You are an expert AI Dungeon Master and Data Architect. Your task is to convert unstructured D&D adventure text into a fully structured, playable JSON Graph.

# Core Directive: Inference & Completion
The input text may be narrative and lack explicit structured data. **You must infer missing details based on context.**
1. **Environment**: If the text says "stormy", infer `sound="Thunder"`, `light="Dim"`. If "cave", infer `light="Darkness"`.
2. **Mechanics**: If the text implies a challenge (e.g., "persuade the guard"), you must create an `InteractionSpec` even if a specific DC isn't listed (estimate a standard DC based on difficulty).
3. **Transitions**: If the text moves from one location to another without naming them, you must **invent** logical IDs (e.g., `scene_beach` -> `scene_temple`) and link them.

# Data Structure Rules

## 1. Scene Segmentation
Break the text into a **JSON List** of Scene Nodes. Create a new node whenever:
* The location changes.
* A specific encounter (Combat/Social) begins.
* The narrative "chapter" shifts.

## 2. Field Extraction Guidelines
* **id**: Snake_case unique identifier (e.g., `merrow_encounter`).
* **type**: `encounter` (default), `roleplay`, `transition`, or `puzzle`.
* **read_aloud**: Extract text meant to be read to players (often in quotes or specific blocks). **Do NOT** put rules or secrets here.
* **gm_guidance**: Summarize DM-facing info, secrets, tactics, and "what happens if..." scenarios.
* **environment**:
    * `light`: "Bright", "Dim", "Darkness", etc. (Infer from time of day/weather).
    * `terrain`: "Normal", "Difficult (Sand)", "Water", etc.
    * `sound`: Inferred ambient sounds.
* **entities**:
    * Extract explicit enemies/NPCs.
    * **Ignore** stat blocks (HP, AC). Only keep `name`, `count`, and generate a `ref_slug` (e.g., "Merrow" -> "merrow").
    * Infer `disposition`: "hostile", "friendly", or "neutral".
    * `state`: Initial position or activity (e.g., "Hiding", "Sleeping").
* **interactions**:
    * Convert text like "DC 15 Charisma check" into structured objects.
    * Format: `{ "trigger": "Negotiate", "mechanic": "DC 15 Charisma", "success": "Cost reduced" }`
* **loot**: If specific items are mentioned, list them. If none, use `[]`.

# Output Format
Return **ONLY** a valid JSON List. Do not wrap in markdown blocks if possible.

[
  {
    "id": "unique_scene_id",
    "title": "Scene Title",
    "type": "encounter",
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

# ================== Logger Setup ==================
log_dir = "logs"
os.makedirs(log_dir, exist_ok=True)
log_filename = os.path.join(log_dir, f"story_gen_{datetime.now().strftime('%Y%m%d_%H%M%S')}.log")

logging.basicConfig(
    filename=log_filename,
    filemode="w",
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
)

# ================== Helper Functions ==================

def clean_json_text(text: str) -> str:
    """
    LLMs often wrap JSON in ```json ... ``` blocks. 
    This function strips them to ensure parsing works.
    """
    # Remove markdown code blocks
    pattern = r"```(?:json)?\s*([\s\S]*?)\s*```"
    match = re.search(pattern, text)
    if match:
        return match.group(1)
    return text.strip()

def call_llm_generation(input_text: str) -> str:
    """
    Calls OpenAI to convert text -> JSON List.
    """
    client = OpenAI(api_key=os.environ.get("OPENAI_API_KEY"))
    
    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": f"Please process the following adventure text:\n\n{input_text}"}
    ]

    logging.info("Sending request to LLM...")
    response = client.chat.completions.create(
        model="gpt-4o",  # gpt-4o is highly recommended for complex schema inference
        messages=messages,
        temperature=0.1, # Low temperature for consistent structure
    )
    return response.choices[0].message.content

# ================== Main Test Execution ==================

def main():
    # 1. The Raw Input Text (From your Dragon's Rest example)
    raw_adventure_text = """
Run the Encounter
Let the players take the lead! They can try anything they can imagine.
... (Assume your full Merrow + Arrival + Zombie text is here) ...
(For brevity in the code file, I will just reference that we are passing the full text provided previously)
    """
    
    # 这里的 raw_adventure_text 请替换为你完整的《风暴残骸岛》文本
    # 为了演示，我写一个缩略版，实际运行请把那个长文本粘进来
    raw_adventure_text = """
Run the Encounter
Let the players take the lead! They can try anything they can imagine.

Encourage the players to tell you their ideas. Ask them to let you know what they are thinking, and then run with it!

Whatever the characters try and whatever their die rolls indicate, use vivid descriptions to keep things exciting.

Here are some of the most likely approaches.

Negotiate with the Merrow
The players might agree to pay tribute to the Scaled Queen. The merrow initially demands a payment of 400 gold pieces (gp) or its equivalent in goods. This is the value of all the goods in the hold. The characters can use Charisma checks (applying Persuasion, Intimidation, or perhaps Deception skills, as appropriate) to get it to accept a lower amount; each successful DC 15 check reduces the amount he asks for by 100 gp.

If the players ask about the Scaled Queen, the merrow says that she is a huge, two-headed merrow who carries the special blessing of the Prince of Demons, Demogorgon.

Attack the Merrow
Lots of players will attack first. Other players will decide their characters attack if other approaches fail. When a player decides that their character attacks, their character acts first in combat. Then play passes to the right. Take the merrow’s turn when it passes around to you.

On each player’s turn, talk through the different actions their character can take, such as attacking with weapons and casting spells. Explain the character’s different weapons and spell attacks.

This merrow looks fearsome but doesn't present too much of a threat to the characters. They should be able to defeat it in 1–2 rounds.

Merrow Extortionist
Large Monstrosity, Typically Chaotic Evil

Armor Class 13 (natural armor)

Hit Points 30 (4d10 + 8)

Speed 10 ft., swim 40 ft.

STR

16 (+3)

DEX

10 (+0)

CON

15 (+2)

INT

8 (−1)

WIS

10 (+0)

CHA

9 (−1)

Senses darkvision 60 ft., passive Perception 10

Languages Abyssal, Aquan, Common

Challenge 1 (100 XP) Proficiency Bonus +2

Amphibious. The merrow can breathe air and water.

Actions

Multiattack. The merrow makes two Rend attacks.

Rend. Melee Weapon Attack: +5 to hit, reach 10 ft., one target. Hit: 8 (2d4 + 3) piercing damage.

Linda Lithén

Shenanigans
The players might think of creative ideas for dealing with the merrow:

Can I roll a barrel to push the merrow overboard?
Can I drop a sail on it so it can't see?
Can I persuade the crew to rush it?
Can I...
Whatever the players ask, the answer should (almost) always be something like, “You can try!” If a character has a specific spell or ability that will let them accomplish what they want to do, help the player use that spell or ability. Otherwise, ask the player to make a ability check that's appropriate to their character's tactics, such as a Strength (Athletics) check for trying to roll the barrel at the merrow, a Dexterity check to fling a sail over it, or a Charisma (Intimidation) check to convince it that the crew is about to attack.

Wrap Up
When the characters have dealt with the merrow, one way or another, read this text to wrap up:

With a splash, the merrow disappears into the ocean deeps and the ship continues on its way. The sailors raise a cheer, and the storm brewing overhead seems not quite so threatening now.

And that brings us to the end of your first taste of adventure.

Welcome to Dragon’s Rest
Jenn Ravenna

The adventure continues at a tiny cloister on Stormwreck Isle called Dragon’s Rest, a haven where world-weary people come to seek peace, reconciliation, and enlightenment. There, the characters learn about the dangers facing Stormwreck Isle.

Each character has a specific reason for coming to the cloister, as shown on the character sheets. You can also let players invent their own reasons for their characters to seek out the wisdom and assistance of Elder Runara, who runs the cloister.

Read the following text when you’re ready to start:

Stormwreck Isle—now visible off the bow—promises rare wonders. Seaweed shimmers in countless brilliant colors below you, and rays of sunlight defy the overcast sky to illuminate the lush grass and dark basalt rock of the island. Avoiding the rocks jutting up from the ocean, your ship makes its way toward a calm harbor on the island’s north side.

A large, open-air temple comes into view, perched on the edge of a cliff high above you. The ship drops anchor at the mouth of the harbor, and two sailors row you ashore. You have plenty of time to admire the towering statue at the center of the temple, depicting a wizened man surrounded by seven songbirds. A long path winds up the side of the cliff to the temple, dotted along the way with doorways cut into the rock.

The sailors set you ashore on a rickety dock, where a large rowboat is neatly tied. They point to the base of the path and wish you good luck before they row back to the ship. Your visit to Dragon’s Rest begins!

Before continuing with the adventure, encourage the players to introduce their characters to each other if they haven’t done so already. They might want to discuss their reasons for visiting Dragon’s Rest, or they might prefer to keep their reasons secret for now.

Ask the players to give you the party’s marching order as they start toward the cloister. Who’s in front, and who’s bringing up the rear? Make a note of this marching order.

When you’re ready, continue with the “Drowned Sailors” section.

Drowned Sailors
Read the following text to start the encounter:

As you’re about to leave the beach and start your climb, you hear a ruckus of splashing and a wet, gurgling moan behind you. Three figures are shambling up from the water’s edge, about thirty feet away. They’re dressed as sailors, but their skin is gray and they look drowned. Sea water drools from their slack mouths as they lurch toward you.

The three shambling sailors are zombies, the animated corpses of sailors who died in a recent shipwreck. The characters face a choice: they can turn and fight the zombies, or they can continue up the path and leave the slow, shambling zombies behind.

If the characters turn and fight, this is the first combat encounter in the adventure. Here are the steps you should follow to run it:

Review the zombie stat block below.
Use the initiative rules to determine who acts first, second, third, and so on. Keep track of everyone’s initiative count on your notepad.
On the zombies’ initiative count, they move toward the characters. If they get close enough, they make melee attacks. The zombies’ stat block contains the information you need to resolve these attacks.
On each character’s initiative count, the character can choose from the actions on their character sheet.
The zombies fight until they’re all defeated.
Tip: Undead Fortitude. The zombies’ Undead Fortitude trait reflects how hard it is to kill these walking corpses. When this trait prevents a zombie from dying, give the players a hint about what happened. You might say, “That should have finished the creature off, but it refuses to stop moving!” On the flip side, any time a zombie takes radiant damage (such as from the cleric’s sacred flame cantrip), you might describe the creature howling in agony. This can help the players realize that radiant damage is a way to get around Undead Fortitude. If the players ask whether their characters know anything about fighting zombies, have them make DC 10 Intelligence checks. Those who succeed might recall that a particularly powerful blow (a critical hit) or radiant damage can help finish off a zombie.

Runara’s Aid. In the unlikely event that the zombies defeat the adventurers, Elder Runara comes to their rescue. The characters wake up in a temple in Dragon’s Rest. Runara explains that she heard the sounds of combat and arrived just in time to prevent the zombies from dragging the characters into the sea.

Avoiding the Zombies. If the characters are faring poorly against the zombies or decide not to fight them, the characters can easily escape from the slow, shambling monsters. The zombies don’t follow them up the path toward Dragon’s Rest.
    """

    print(">>> 1. Calling LLM to generate Story Graph JSON...")
    llm_output = call_llm_generation(raw_adventure_text)
    
    # Log the raw output
    logging.info(f"[LLM RAW OUTPUT]\n{llm_output}")
    
    # 2. Clean and Parse
    print(">>> 2. Cleaning and Parsing JSON...")
    json_str = clean_json_text(llm_output)
    
    try:
        # 3. Load into Graph
        print(">>> 3. Loading into StoryGraph...")
        graph = StoryGraph()
        nodes = graph.add_scenes_from_json_list(json_str)
        
        print(f"    Success! Loaded {len(nodes)} scenes.")
        print(f"    Scene IDs: {graph.list_scene_ids()}")
        
        # 4. Validation
        print(">>> 4. Validating Graph Logic...")
        warnings = graph.validate_graph()
        if warnings:
            print("    [WARNINGS Found]:")
            for w in warnings:
                print(f"      - {w}")
        else:
            print("    Graph structure is valid (no dangling edges).")
            
        # 5. Visualization (Mermaid)
        print(">>> 5. Generating Mermaid Diagram...")
        mermaid_code = graph.to_mermaid()
        print("\n" + "="*40)
        print("MERMAID CODE (Copy to Mermaid Live Editor)")
        print("="*40)
        print(mermaid_code)
        print("="*40 + "\n")
        
        # Optional: Print details of the first node to check inference quality
        if nodes:
            first = nodes[0]
            print(f"checking inference on first node '{first.title}':")
            print(f"  - Environment: {first.environment}")
            print(f"  - Interactions: {len(first.interactions)} found.")
            print(f"  - Entities: {[e.name for e in first.entities]}")

        print(">>> 6. Saving to adventure_data.json...")     
        output_file = "adventure_data.json"                  
        with open(output_file, "w", encoding="utf-8") as f:  
            f.write(graph.to_json(indent=2))                 
        print(f"    Saved successfully to {output_file}")    


    except json.JSONDecodeError as e:
        print("!!! JSON Parse Error. Check logs for raw output.")
        print(e)
    except Exception as e:
        print(f"!!! Unexpected Error: {e}")

if __name__ == "__main__":
    main()