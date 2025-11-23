# run_workflow_live_test.py
import sys
import os
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from app.engine.agent_workflow import answer_query

if __name__ == "__main__":
    tests = [
        # Monsters
        "Tell me about the monster called Merrow.",
        "Explain the monster 'Adult Red Dragon'.",
        "Show me information about 'Zombie' (prefer 2014 SRD).",

        # Spells
        "Please explain the spell Fireball in DnD 5e.",
        "What does the spell Shield do?",
        "Explain the spell 'Mage Hand'.",

        # Equipment
        "What is Studded Leather Armor in DnD 5e? (prefer 2014 SRD).",
        "Explain the item 'Longsword'.",
        "Give details about 'Crossbow, light'.",

        # Backgrounds
        "What is the Sage background in DnD 5e?",
        "Explain the Soldier background.",
        "Describe the Acolyte background.",

        # Classes
        "Tell me about the Wizard class.",
        "Show me details about the Barbarian class.",
        "Explain the Cleric class.",

        # Conditions
        "What does the Grappled condition do in DnD 5e?",
        "Explain the Invisible condition.",
        "What is the Prone condition?",

        # Races
        "Tell me about the Elf race.",
        "Explain the Dwarf race.",
        "Describe the Dragonborn race.",

        # Sections (SRD rulebook sections)
        "Show me the Combat rules section.",
        "Explain the Adventuring rules section.",
        "What is in the Spellcasting rules?",

        # Spelllist (class spell lists)
        "Show me the Bard spell list.",
        "What spells does the Cleric get at level 1?",
        "Give me the Wizard spell list (SRD only).",

        # Documents
        "What is the SRD 5.1 document?",
        "Tell me about the document 'Monsters' from 2014 SRD.",
        "Explain the 'Spells' document from SRD.",
    ]

    for q in tests:
        print("=" * 80)
        print("USER:", q)
        print("=" * 80)
        out = answer_query(q, max_tool_steps=6)
        print("\nASSISTANT:", out)
        print()
