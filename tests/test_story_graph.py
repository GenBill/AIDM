# test_story_graph.py
from app.engine.story import StoryGraph

def main():
    g = StoryGraph()

    scene_json_1 = """
    {
      "id": "scene_001",
      "title": "Goblin Ambush on the Road",
      "summary": "The party travels along a forest road when goblins ambush them from the bushes.",
      "enemies": [
        {"name": "Goblin", "ref_type": "monsters", "ref_slug": "goblin", "count": 4}
      ],
      "next": [
        {"to": "scene_002", "weight": 1.0, "label": "Follow the tracks"},
        {"to": "scene_003", "weight": 1.0, "label": "Return to town"}
      ]
    }
    """

    scene_json_2 = """
    {
      "id": "scene_002",
      "title": "Goblin Cave",
      "summary": "The party follows the tracks to a small cave where more goblins live.",
      "enemies": [
        {"name": "Goblin Boss", "ref_type": "monsters", "ref_slug": "goblin-boss", "count": 1}
      ],
      "next": [
        {"to": "scene_004", "weight": 1.0, "label": "Defeat the boss and rescue prisoners"}
      ]
    }
    """

    g.add_scene_from_json_str(scene_json_1)
    g.add_scene_from_json_str(scene_json_2)

    print("Scene IDs:", g.list_scene_ids())
    print("Adjacency list:", g.to_adjacency_list())
    print("Is DAG:", g.check_is_dag())
    print("JSON dump:\n", g.to_json())

if __name__ == "__main__":
    main()
