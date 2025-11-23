# test_combat_tools.py
import pprint
import os
import sys
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import app.engine.combat as combat


def banner(title: str):
    print("=" * 80)
    print(title)
    print("=" * 80)


def test_dice():
    banner("DICE ROLLER TESTS")

    tests = [
        ("1d6", 5),
        ("1d7", 5),
        ("2d10", 5),
        ("3d6+2", 5),
        ("1d20+5", 5),
    ]

    for expr, times in tests:
        print(f"\nRolling {expr!r} {times} times:")
        for i in range(times):
            res = combat.roll_dice(expr)
            print(f"  roll {i+1}: {res}")


def test_hp_and_conditions():
    banner("HP / CONDITIONS TESTS")

    # 重置全局战斗状态
    combat.reset_combat_state()

    # 创建两个角色
    combat.upsert_actor("hero", "Hero", max_hp=30, armor_class=16)
    combat.upsert_actor("goblin", "Goblin", max_hp=15, armor_class=13)

    print("\nInitial state (list_actors):")
    pprint.pprint(combat.list_actors())

    # 对 goblin 造成 8 点伤害
    print("\nApplying damage 8 to goblin...")
    damage_result = combat.apply_damage("goblin", 8, damage_type="slashing")
    pprint.pprint(damage_result)

    print("\nState after damage:")
    pprint.pprint(combat.list_actors())

    # 治疗 goblin 5 点
    print("\nHealing goblin 5 HP...")
    heal_result = combat.heal_actor("goblin", 5)
    pprint.pprint(heal_result)

    print("\nState after heal:")
    pprint.pprint(combat.list_actors())

    # 给 hero 加一个 condition
    print("\nAdding condition 'grappled' to hero...")
    cond_add = combat.add_condition("hero", "grappled")
    pprint.pprint(cond_add)

    # 再移除
    print("\nRemoving condition 'grappled' from hero...")
    cond_rm = combat.remove_condition("hero", "grappled")
    pprint.pprint(cond_rm)

    print("\nFinal state:")
    pprint.pprint(combat.list_actors())


def main():
    test_dice()
    test_hp_and_conditions()


if __name__ == "__main__":
    main()
