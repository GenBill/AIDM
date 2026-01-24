# app/engine/fight_agent.py
import os
import json
from typing import Any, Dict, List, Optional, Tuple

from openai import OpenAI
from app.api.deepseek import DeepSeek

from app.engine.session import session_manager
from app.schemas import DMResponse
from app.config import STORIES_DIR
from app.engine.combat import resolve_attack, roll_dice

if os.getenv("OPENAI_API_KEY"):
    MODEL_NAME = "gpt-5.1"
    client = OpenAI()
elif os.getenv("DEEPSEEK_API_KEY"):
    MODEL_NAME = "deepseek-chat" 
    client = DeepSeek()
else:
    raise ValueError("No API key found for OpenAI or DeepSeek")

# ---- SYSTEM PROMPTS ----

PLANNER_SYSTEM_PROMPT = """
You are a STRICT D&D 5e combat planner.

Your job this turn is ONLY:
- Interpret the player's natural-language intent.
- Choose EXACTLY ONE action for the player (attack / cast_spell / talk / flee / other).
- Choose EXACTLY ONE action for the enemy (usually an attack).
- Optionally mark whether combat should end.

You MUST NOT roll dice or apply damage yourself. The game engine will do all math.

VERY IMPORTANT RULES (NO HALLUCINATIONS):
- You MUST ONLY choose player attacks whose names already exist in `player_attacks`.
- You MUST ONLY choose enemy actions whose names already exist in `enemy_actions` and whose value is not null.
- Do NOT invent new attack names, new spells, or new damage dice.
- If the player describes something impossible with the available options,
  pick the closest reasonable option OR set kind = "other" and no attack is made.

You will receive:
- Player stats and full list of attacks.
- Enemy stats and full list of actions.
- Current HP / AC information.
- The last DM narration from the combat log.
- The player's latest message.

Return a SINGLE JSON object with this EXACT SCHEMA:

{
  "player_action": {
    "kind": "attack" | "cast_spell" | "defend" | "talk" | "flee" | "other",
    "attack_name": "string or null",
    "target": "string or null"
  },
  "enemy_action": {
    "kind": "attack" | "talk" | "flee" | "other",
    "action_name": "string or null",
    "target": "string or null"
  },
  "combat_state": {
    "should_end": true or false,
    "end_reason": "enemy_dead" | "player_dead" | "escape" | "truce" | null
  }
}

- If player_action.kind is "attack" or "cast_spell":
  - attack_name MUST be the exact name of one entry in `player_attacks[].name`.
  - target MUST be the name of an existing creature (e.g., the enemy).
- If player_action.kind is "talk" / "flee" / "other":
  - attack_name should be null.

- If enemy_action.kind is "attack":
  - action_name MUST be the exact name of one entry in `enemy_actions[].name`.
  - target is usually the player.

If you are unsure, choose the simplest reasonable option. Do NOT invent new objects.
"""

NARRATOR_SYSTEM_PROMPT = """
You are a vivid but RULE-RESPECTING D&D 5e combat narrator.

You will be given:
- The structured summary of this combat round.
- Which attacks were attempted, which hit, and how much damage was dealt.
- HP of each side before and after the round.

Your job:
- Describe ONLY what actually happened according to the provided data.
- Do NOT invent extra attacks, spells, or effects.
- Do NOT change HP numbers; just describe them.
- Use 2-5 sentences, 2nd person ("you").
- Always end by briefly asking the player what they do next.

Example ending:
"Bloodied but unbroken, you still stand. What do you do now?"
"""


class FightAgent:
    def _get_last_dm_message(self, session) -> Optional[str]:
        """从 chat_history 里拿最后一条 DM / assistant 的叙述，给 planner 当上下文。"""
        for msg in reversed(session.chat_history):
            if msg.get("role") in ("assistant", "dm"):
                return msg.get("content")
        return None

    def _build_player_attack_map(self, player) -> Dict[str, Any]:
        """把玩家 attacks 变成 name -> attack 对应表，方便按名字查."""
        attack_map: Dict[str, Any] = {}
        for atk in getattr(player.character_sheet, "attacks", []) or []:
            # Attack 是 Pydantic model，保留原对象，Python 里用属性
            name = getattr(atk, "name", None)
            if not name:
                continue
            attack_map[name] = atk
        return attack_map

    def _build_enemy_action_map(self, enemy_actions: List[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
        """敌人 actions 列表 -> name -> action dict."""
        action_map: Dict[str, Dict[str, Any]] = {}
        for act in enemy_actions:
            name = act.get("name")
            if not name:
                continue
            action_map[name] = act
        return action_map

    def _choose_default_enemy_attack(self, enemy_actions: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
        """
        如果 LLM 没给出可用的 enemy action，就从 actions 里选一个有 attack_bonus 和 damage_dice 的。
        比如 Merrow 的 'Rend'。
        """
        for act in enemy_actions:
            if act.get("attack_bonus") is not None and act.get("damage_dice"):
                return act
        return None

    def _call_planner(
        self,
        player,
        enemy_name: str,
        enemy_stats: Dict[str, Any],
        enemy_hp: int,
        last_dm: Optional[str],
        player_input: str,
    ) -> Dict[str, Any]:
        """调用第一次 LLM，生成本回合的 player_action / enemy_action / combat_state 计划."""
        # 准备给 LLM 的精简攻击列表（只要名字 + 攻击加值 + 伤害骰）
        print("=== PLANNER CALLED ===")
        player_attacks_list: List[Dict[str, Any]] = []
        for atk in getattr(player.character_sheet, "attacks", []) or []:
            player_attacks_list.append(
                {
                    "name": getattr(atk, "name", None),
                    "attack_bonus": getattr(atk, "bonus", None),
                    "damage_dice": getattr(atk, "damage", None),
                }
            )

        enemy_actions: List[Dict[str, Any]] = enemy_stats.get("actions", []) or []

        context_obj = {
            "player": {
                "name": player.name,
                "hp": player.current_hp,
                "ac": player.character_sheet.ac,
                "attacks": player_attacks_list,
            },
            "enemy": {
                "name": enemy_name,
                "hp": enemy_hp,
                "ac": enemy_stats.get("ac", 12),
                "actions": enemy_actions,
            },
            "last_dm_narration": last_dm,
            "player_input": player_input,
        }
        print("=== PLANNER CONTEXT ===")
        print(json.dumps(context_obj, indent=2))
        messages = [
            {"role": "system", "content": PLANNER_SYSTEM_PROMPT},
            {
                "role": "user",
                "content": "Here is the current combat context as JSON:\n```json\n"
                + json.dumps(context_obj, indent=2)
                + "\n```\n\nReturn ONLY the planning JSON object described in the instructions.",
            },
        ]

        completion = client.chat.completions.create(
            model=MODEL_NAME,
            messages=messages,
            response_format={"type": "json_object"},
        )
        raw_text = completion.choices[0].message.content or "{}"

        print("=== PLANNER RAW RESPONSE ===")
        print(raw_text)
        try:
            plan = json.loads(raw_text)
        except Exception:
            plan = {}

        return plan

    def _run_attack_and_log(
        self,
        attacker_name: str,
        attack_bonus: int,
        damage_dice: str,
        target_name: str,
        target_ac: int,
    ) -> Tuple[int, str, Dict[str, Any]]:
        """
        用 resolve_attack 真实计算一轮攻击，返回 (damage, log_str, result_dict)
        """
        print("=== DEBUG ATTACK ===")
        print(f"Attacker: {attacker_name}")
        print(f"Target: {target_name}")
        print(f"attack_bonus(raw): {attack_bonus}")
        print(f"attack_bonus(int): {int(attack_bonus) if attack_bonus is not None else None}")
        print(f"target_ac(raw): {target_ac}")
        print(f"target_ac(int): {int(target_ac) if target_ac is not None else None}")
        print(f"damage_dice(raw): {damage_dice}")
        print(f"damage_dice(clean): {damage_dice.replace(' ', '')}")
        print("=====================")
        clean_dice = damage_dice.replace(" ", "")
        result = resolve_attack(
            attacker_name=attacker_name,
            attack_name=f"{attacker_name}'s attack",
            attack_bonus=int(attack_bonus),
            target_name=target_name,
            target_ac=int(target_ac),
            damage_dice=clean_dice,
        )
        dmg = result["damage_dealt"] if result.get("is_hit") else 0
        log_str = result.get("log", "")
        return dmg, log_str, result

    def _call_narrator(
        self,
        round_summary: Dict[str, Any],
    ) -> str:
        """第二次 LLM 调用：给出本回合 summary，让 LLM 只负责讲故事。"""
        messages = [
            {"role": "system", "content": NARRATOR_SYSTEM_PROMPT},
            {
                "role": "user",
                "content": "Here is the structured summary of this combat round:\n```json\n"
                + json.dumps(round_summary, indent=2)
                + "\n```\n\nWrite the narration as instructed.",
            },
        ]

        completion = client.chat.completions.create(
            model="gpt-5.1",
            messages=messages,
        )
        return (completion.choices[0].message.content or "").strip()

    def process_fight_round(self, session_id: str, player_input: str) -> DMResponse:
        session = session_manager.load_session(session_id)
        player = session.players[0]

        # ---- 载入当前战斗节点 ----
        story_path = STORIES_DIR / session.story_id / "story.json"
        with open(story_path, "r", encoding="utf-8") as f:
            story_data = json.load(f)
        current_node = story_data["nodes"].get(session.current_node_id, {})

        # ==== 1. 识别敌人（只看 type == "monster"） ====
        entities = current_node.get("entities", []) or []
        enemies = [e for e in entities if e.get("type") == "monster"]

        if not enemies:
            narrative = "There are no hostile monsters here. Combat seems to be over."
            dm = DMResponse(
                narrative=narrative,
                mechanics_log=None,
                damage_taken=0,
                transition_to_id=None,
                active_mode="action",
            )
            session.chat_history.append({"role": "assistant", "content": narrative})
            session_manager.save_session(session)
            return dm

        # 暂时只支持第一个怪物为目标
        target_enemy = enemies[0]
        enemy_name = target_enemy.get("name", "Enemy")
        enemy_stats = target_enemy.get("stats", {}) or {}

        enemy_max_hp = enemy_stats.get("hp_max", 30)
        enemy_ac = enemy_stats.get("ac", 12)

        # ---- 敌人当前 HP （来自 session.enemy_states）----
        if session.enemy_states is None:
            session.enemy_states = {}

        enemy_state = session.enemy_states.get(enemy_name, {"damage_taken": 0})
        enemy_damage_taken = enemy_state.get("damage_taken", 0)
        enemy_current_hp = max(0, enemy_max_hp - enemy_damage_taken)

        # 如果敌人已经死了但还在 /fight
        if enemy_current_hp <= 0:
            narrative = f"{enemy_name} already lies defeated. There is nothing left to fight here."
            dm = DMResponse(
                narrative=narrative,
                mechanics_log=None,
                damage_taken=0,
                transition_to_id=None,
                active_mode="action",
            )
            session.chat_history.append({"role": "assistant", "content": narrative})
            session_manager.save_session(session)
            return dm

        # ==== 2. Planner LLM：决定这一回合双方动作 ====
        last_dm = self._get_last_dm_message(session)

        plan = self._call_planner(
            player=player,
            enemy_name=enemy_name,
            enemy_stats=enemy_stats,
            enemy_hp=enemy_current_hp,
            last_dm=last_dm,
            player_input=player_input,
        )

        player_action = plan.get("player_action", {}) or {}
        enemy_action = plan.get("enemy_action", {}) or {}
        combat_state = plan.get("combat_state", {}) or {}

        # ==== 3. 软件工程：根据 plan 和 JSON 做真正的攻击计算 ====
        mechanics_logs: List[str] = []
        player_damage_taken_this_round = 0
        enemy_damage_taken_this_round = 0

        # —— 玩家攻击 —— 
        player_attack_map = self._build_player_attack_map(player)
        pa_kind = player_action.get("kind", "other")
        pa_attack_name = player_action.get("attack_name")

        player_attack_result: Optional[Dict[str, Any]] = None
        enemy_attack_result: Optional[Dict[str, Any]] = None

        if pa_kind in ("attack", "cast_spell") and pa_attack_name in player_attack_map:
            atk_obj = player_attack_map[pa_attack_name]
            atk_bonus = getattr(atk_obj, "bonus", None)
            dmg_dice = getattr(atk_obj, "damage", None)

            if atk_bonus is not None and dmg_dice:
                dmg, log_str, result = self._run_attack_and_log(
                    attacker_name=player.name,
                    attack_bonus=atk_bonus,
                    damage_dice=dmg_dice,
                    target_name=enemy_name,
                    target_ac=enemy_ac,
                )
                enemy_damage_taken_this_round += dmg
                mechanics_logs.append(log_str)
                player_attack_result = result

        # —— 敌人攻击 —— 
        enemy_actions: List[Dict[str, Any]] = enemy_stats.get("actions", []) or []
        enemy_action_map = self._build_enemy_action_map(enemy_actions)
        ea_kind = enemy_action.get("kind", "attack")  # 默认攻击
        ea_name = enemy_action.get("action_name")

        # 如果 LLM 选的 action_name 不可用，退回到第一个有数值的攻击动作
        enemy_attack_dict: Optional[Dict[str, Any]] = None
        if ea_kind == "attack" and ea_name in enemy_action_map:
            enemy_attack_dict = enemy_action_map[ea_name]
        elif ea_kind == "attack":
            enemy_attack_dict = self._choose_default_enemy_attack(enemy_actions)

        if enemy_attack_dict:
            atk_bonus = enemy_attack_dict.get("attack_bonus")
            dmg_dice = enemy_attack_dict.get("damage_dice")
            if atk_bonus is not None and dmg_dice:
                dmg, log_str, result = self._run_attack_and_log(
                    attacker_name=enemy_name,
                    attack_bonus=atk_bonus,
                    damage_dice=dmg_dice,
                    target_name=player.name,
                    target_ac=player.character_sheet.ac,
                )
                player_damage_taken_this_round += dmg
                mechanics_logs.append(log_str)
                enemy_attack_result = result

        # ---- 更新 HP 状态 ----
        # 玩家
        old_player_hp = player.current_hp
        if player_damage_taken_this_round > 0:
            player.current_hp = max(0, player.current_hp - player_damage_taken_this_round)

        # 敌人
        if enemy_damage_taken_this_round > 0:
            new_total_damage = enemy_damage_taken + enemy_damage_taken_this_round
            session.enemy_states[enemy_name] = {"damage_taken": new_total_damage}
            enemy_current_hp_after = max(0, enemy_max_hp - new_total_damage)
        else:
            enemy_current_hp_after = enemy_current_hp

        # ==== 4. Narrator LLM：根据结果叙述 ====
        round_summary = {
            "player": {
                "name": player.name,
                "hp_before": old_player_hp,
                "hp_after": player.current_hp,
            },
            "enemy": {
                "name": enemy_name,
                "hp_before": enemy_current_hp,
                "hp_after": enemy_current_hp_after,
            },
            "player_action": player_action,
            "enemy_action": enemy_action,
            "attack_results": {
                "player_attack": player_attack_result,
                "enemy_attack": enemy_attack_result,
            },
            "mechanics_log": mechanics_logs,
        }

        narrative_text = self._call_narrator(round_summary)

        # ==== 5. 结束状态 & active_mode ====

        player_dead = player.current_hp <= 0
        enemy_dead = enemy_current_hp_after <= 0

        active_mode = "fight"
        if enemy_dead or player_dead or (
            combat_state.get("should_end")
            and combat_state.get("end_reason") in ["enemy_dead", "player_dead", "truce", "escape"]
        ):
            active_mode = "action"
            if enemy_dead and "defeat" not in narrative_text.lower():
                narrative_text += f"\n\n(System: {enemy_name} has been defeated!)"
            if player_dead and "unconscious" not in narrative_text.lower():
                narrative_text += "\n\n(System: You fall to 0 HP and drop unconscious.)"

        # 拼 mechanics_log 文本
        mechanics_log_str = "\n".join(mechanics_logs) if mechanics_logs else None

        dm_response = DMResponse(
            narrative=narrative_text,
            mechanics_log=mechanics_log_str,
            damage_taken=player_damage_taken_this_round,
            transition_to_id=None,
            active_mode=active_mode,
        )
        print("=== ACTIVE MODE ===")
        print(active_mode )
        # ==== 6. 写入 session.chat_history 并保存 ====
        session.chat_history.append({"role": "user", "content": f"[Encounter] {player_input}"})
        if dm_response.mechanics_log:
            session.chat_history.append({"role": "data", "content": dm_response.mechanics_log})
        session.chat_history.append({"role": "assistant", "content": dm_response.narrative})

        session_manager.save_session(session)
        return dm_response


fight_agent = FightAgent()
