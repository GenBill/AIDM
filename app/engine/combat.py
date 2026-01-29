# aidnd_combat_tools.py
"""
Combat-related tools for AI DnD Master.

These tools are *separate* from catalog / Open5e tools and focus on
runtime combat mechanics:

- Dice rolling (trusted, reproducible)
- Simple combat state tracking (HP, temp HP, conditions)
- State query & writeback to a small JSON "database"

The idea is that the LLM calls these as tools during combat, while
catalog lookups (monsters, spells, etc.) live in `aidnd_catalog_tools.py`.
"""

from __future__ import annotations

import json
import random
import re
from pathlib import Path
from typing import Any, Dict, List, Optional
from app.engine.i18n import get_text # <--- Import i18n


# ---------------------------------------------------------------------------
# Paths & basic persistence
# ---------------------------------------------------------------------------

BASE = Path(".")
STATE_DIR = BASE / "state"
STATE_DIR.mkdir(exist_ok=True)
STATE_PATH = STATE_DIR / "combat_state.json"


def _load_state() -> Dict[str, Any]:
    """Load combat state from disk; if missing, return empty structure."""
    if not STATE_PATH.exists():
        return {"actors": {}}  # actors keyed by actor_id
    try:
        return json.loads(STATE_PATH.read_text(encoding="utf-8"))
    except Exception:
        # If the file is corrupted, fail safe by resetting
        return {"actors": {}}


def _save_state(state: Dict[str, Any]) -> None:
    """Persist combat state back to disk."""
    STATE_PATH.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")


# ---------------------------------------------------------------------------
# Dice roller
# ---------------------------------------------------------------------------

DICE_TERM_RE = re.compile(
    r"""
    (?P<sign>[+-]?)\s*
    (?:
        (?:(?P<num>\d*)[dD](?P<sides>\d+))   # XdY / dY
        |
        (?P<flat>\d+)                       # plain integer
    )
    """,
    re.VERBOSE,
)


def roll_dice(expr: str, seed: Optional[int] = None) -> Dict[str, Any]:
    """
    Roll a dice expression and return detailed results.

    Supported examples (case-insensitive, spaces allowed):
      - "1d20"
      - "d20 + 5"
      - "2D6+1D4+3"
      - "1d7 2d10"   (spaces are treated like '+')
      - "+2d8 -1"    (leading + / - allowed)

    NOT supported (for now): advantage/disadvantage, keep highest, etc.

    Returns a dict:
      {
        "expression": original_expr,
        "normalized": normalized_expr,
        "total": int,
        "terms": [
          {
            "term": "1d20",
            "sign": "+",
            "num": 1,
            "sides": 20,
            "rolls": [17],
            "subtotal": 17
          },
          {
            "term": "+5",
            "sign": "+",
            "flat": 5,
            "subtotal": 5
          },
          ...
        ]
      }
    """
    original = expr
    # Treat whitespace as '+'
    normalized = re.sub(r"\s+", "+", expr.strip())
    if not normalized:
        raise ValueError("Empty dice expression")

    rng = random.Random(seed) if seed is not None else random.SystemRandom()

    total = 0
    terms: List[Dict[str, Any]] = []

    for m in DICE_TERM_RE.finditer(normalized):
        sign_str = m.group("sign") or "+"
        sign = 1 if sign_str != "-" else -1

        if m.group("flat") is not None:
            flat = int(m.group("flat"))
            subtotal = sign * flat
            total += subtotal
            terms.append(
                {
                    "term": f"{sign_str}{flat}",
                    "sign": sign_str or "+",
                    "flat": flat,
                    "subtotal": subtotal,
                }
            )
        else:
            # Dice term
            num_str = m.group("num")
            num = int(num_str) if num_str not in (None, "") else 1  # "d20" -> 1d20
            sides = int(m.group("sides"))
            rolls = [rng.randint(1, sides) for _ in range(num)]
            subtotal = sign * sum(rolls)
            total += subtotal
            terms.append(
                {
                    "term": f"{sign_str}{num}d{sides}",
                    "sign": sign_str or "+",
                    "num": num,
                    "sides": sides,
                    "rolls": rolls,
                    "subtotal": subtotal,
                }
            )

    if not terms:
        raise ValueError(f"Could not parse dice expression: {expr!r}")

    return {
        "expression": original,
        "normalized": normalized,
        "total": total,
        "terms": terms,
    }


# ---------------------------------------------------------------------------
# Combat state helpers
# ---------------------------------------------------------------------------

def upsert_actor(
    actor_id: str,
    name: str,
    max_hp: int,
    armor_class: Optional[int] = None,
    extra: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """
    Create or update an actor in the combat state.

    If the actor already exists, only `max_hp`, `armor_class`, and `extra`
    fields are updated; current HP is preserved unless it was missing.

    Returns the stored actor record.
    """
    state = _load_state()
    actors = state.setdefault("actors", {})

    actor = actors.get(actor_id, {})
    actor.setdefault("conditions", [])
    actor.setdefault("temp_hp", 0)

    actor["id"] = actor_id
    actor["name"] = name
    actor["max_hp"] = int(max_hp)
    actor["hp"] = int(actor.get("hp", max_hp))
    actor["armor_class"] = armor_class
    actor["extra"] = extra or actor.get("extra", {})

    actors[actor_id] = actor
    _save_state(state)
    return actor


def get_actor(actor_id: str) -> Dict[str, Any]:
    """
    Return a single actor's state. If missing, returns {"error": "..."}.
    """
    state = _load_state()
    actor = state.get("actors", {}).get(actor_id)
    if not actor:
        return {"error": f"actor not found: {actor_id}"}
    return actor


def list_actors() -> Dict[str, Any]:
    """
    Return all actors in the current combat.
    """
    state = _load_state()
    return {"actors": state.get("actors", {})}


def apply_damage(
    actor_id: str,
    amount: int,
    damage_type: str = "generic",
) -> Dict[str, Any]:
    """
    Apply damage to an actor, correctly consuming temp HP first.

    HP will never go below 0. Returns a summary:
      {
        "actor_id": ...,
        "name": ...,
        "damage": amount,
        "damage_type": ...,
        "before": {"hp": ..., "temp_hp": ...},
        "after": {"hp": ..., "temp_hp": ...},
    }
    """
    state = _load_state()
    actors = state.setdefault("actors", {})
    actor = actors.get(actor_id)
    if not actor:
        return {"error": f"actor not found: {actor_id}"}

    amount = max(0, int(amount))
    before = {"hp": actor.get("hp", 0), "temp_hp": actor.get("temp_hp", 0)}

    # Temp HP soaks damage first
    temp = actor.get("temp_hp", 0)
    if temp > 0 and amount > 0:
        used = min(temp, amount)
        temp -= used
        amount -= used
        actor["temp_hp"] = temp

    if amount > 0:
        hp = max(0, int(actor.get("hp", 0)) - amount)
        actor["hp"] = hp

    after = {"hp": actor.get("hp", 0), "temp_hp": actor.get("temp_hp", 0)}
    _save_state(state)

    return {
        "actor_id": actor_id,
        "name": actor.get("name"),
        "damage": int(amount),
        "damage_type": damage_type,
        "before": before,
        "after": after,
    }


def heal_actor(
    actor_id: str,
    amount: int,
    allow_overheal: bool = False,
) -> Dict[str, Any]:
    """
    Heal an actor. By default HP cannot exceed max_hp unless `allow_overheal` is True.
    Returns before/after HP.
    """
    state = _load_state()
    actors = state.setdefault("actors", {})
    actor = actors.get(actor_id)
    if not actor:
        return {"error": f"actor not found: {actor_id}"}

    amount = max(0, int(amount))
    before_hp = actor.get("hp", 0)
    max_hp = actor.get("max_hp", before_hp)

    new_hp = before_hp + amount
    if not allow_overheal:
        new_hp = min(new_hp, max_hp)

    actor["hp"] = new_hp
    _save_state(state)

    return {
        "actor_id": actor_id,
        "name": actor.get("name"),
        "heal": int(amount),
        "before_hp": before_hp,
        "after_hp": new_hp,
        "max_hp": max_hp,
    }


def add_condition(actor_id: str, condition: str) -> Dict[str, Any]:
    """
    Add a condition string to the actor (e.g., 'grappled', 'prone').
    """
    state = _load_state()
    actors = state.setdefault("actors", {})
    actor = actors.get(actor_id)
    if not actor:
        return {"error": f"actor not found: {actor_id}"}

    cond = condition.strip().lower()
    conds: List[str] = actor.setdefault("conditions", [])
    if cond not in conds:
        conds.append(cond)
    _save_state(state)
    return {"actor_id": actor_id, "name": actor.get("name"), "conditions": conds}


def remove_condition(actor_id: str, condition: str) -> Dict[str, Any]:
    """
    Remove a condition string from the actor.
    """
    state = _load_state()
    actors = state.setdefault("actors", {})
    actor = actors.get(actor_id)
    if not actor:
        return {"error": f"actor not found: {actor_id}"}

    cond = condition.strip().lower()
    conds: List[str] = actor.setdefault("conditions", [])
    conds = [c for c in conds if c != cond]
    actor["conditions"] = conds
    _save_state(state)
    return {"actor_id": actor_id, "name": actor.get("name"), "conditions": conds}


def reset_combat_state() -> Dict[str, Any]:
    """
    Clear all actors and reset the combat state.
    Useful between encounters or for tests.
    """
    state = {"actors": {}}
    _save_state(state)
    return state

# app/engine/combat.py (追加内容)

def resolve_attack(
    attacker_name: str,
    attack_name: str,
    attack_bonus: int,
    target_name: str,
    target_ac: int,
    damage_dice: str,
    lang: str = "en"  # <--- Add language param
) -> Dict[str, Any]:
    """
    原子化战斗解析工具：处理一次攻击判定 + 伤害计算。
    """
    # 1. 命中判定 (To Hit)
    hit_roll = roll_dice(f"1d20+{attack_bonus}")
    total_hit = hit_roll['total']
    
    d20_val = hit_roll['terms'][0]['rolls'][0]
    is_crit = (d20_val == 20)
    is_fumble = (d20_val == 1)
    
    is_hit = False
    if is_crit:
        is_hit = True
    elif is_fumble:
        is_hit = False
    else:
        is_hit = (total_hit >= target_ac)

    # 构建日志 - using i18n
    log_parts = []
    
    t_attack = get_text(lang, "combat_log", "attack").format(attacker=attacker_name, target=target_name, weapon=attack_name)
    log_parts.append(t_attack)
    
    hit_status = get_text(lang, "combat_log", "miss")
    if is_crit: hit_status = get_text(lang, "combat_log", "crit")
    elif is_hit: hit_status = get_text(lang, "combat_log", "hit")
    
    t_roll = get_text(lang, "combat_log", "roll").format(d20=d20_val, bonus=attack_bonus, total=total_hit, ac=target_ac, result=hit_status)
    log_parts.append(t_roll)

    damage_total = 0
    if is_hit:
        dmg_roll = roll_dice(damage_dice)
        damage_total = dmg_roll['total']
        
        if is_crit:
            damage_total *= 2
            t_dmg = get_text(lang, "combat_log", "damage_crit").format(expr=dmg_roll['normalized'], total=damage_total)
            log_parts.append(t_dmg)
        else:
            t_dmg = get_text(lang, "combat_log", "damage").format(expr=dmg_roll['normalized'], total=damage_total)
            log_parts.append(t_dmg)
    else:
        t_block = get_text(lang, "combat_log", "block")
        log_parts.append(t_block)

    return {
        "is_hit": is_hit,
        "damage_dealt": damage_total,
        "log": "\n".join(log_parts)
    }
