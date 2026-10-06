"""Independent exhaustive oracle for the physical-instance allocation rule."""
from functools import lru_cache
from random import Random

from wuwa_optimizer.optimizer import WEAPON_PULL_CENTS, _assign_weapons


def brute_force_cost(required: tuple[int, ...], owned: tuple[int, ...]) -> int:
    @lru_cache(None)
    def visit(index: int, used: int) -> int:
        if index == len(required):
            return 0
        target = required[index]
        choices = [target + visit(index + 1, used)]
        for asset, rank in enumerate(owned):
            if not (used & (1 << asset)):
                choices.append(max(0, target - rank) + visit(index + 1, used | (1 << asset)))
        return min(choices)
    return visit(0, 0) * WEAPON_PULL_CENTS


def test_physical_matching_agrees_with_exhaustive_oracle():
    random = Random(20261006)
    for _ in range(150):
        required = tuple(random.randint(1, 5) for _ in range(random.randint(1, 6)))
        owned = tuple(random.randint(1, 5) for _ in range(random.randint(0, 6)))
        slots = [{"slot_index": index, "weapon": "shared", "refinement": rank}
                 for index, rank in enumerate(required)]
        inventory = [{"id": f"owned-{index}", "weapon": "shared", "refinement": rank}
                     for index, rank in enumerate(owned)]
        assignments, actions, cost = _assign_weapons(slots, inventory)
        assert cost == brute_force_cost(required, owned)
        assert len({asset["instance_id"] for asset in assignments.values()}) == len(slots)
        assert all(assignments[index]["refinement"] >= rank for index, rank in enumerate(required))
        assert cost == len(actions) * WEAPON_PULL_CENTS
