"""Separate mechanical judge; imports neither solver nor graph utilities."""


def verify(rows, actions):
    traversable, targets, crates, actors = set(), set(), set(), []
    for y, row in enumerate(rows):
        for x, symbol in enumerate(row):
            p = (x, y)
            if symbol not in "# @$.+*":
                return {"valid": False, "reason": "unknown_board_symbol"}
            if symbol != "#":
                traversable.add(p)
            if symbol in ".+*":
                targets.add(p)
            if symbol in "$*":
                crates.add(p)
            if symbol in "@+":
                actors.append(p)
    if len(actors) != 1 or len(crates) != len(targets) or not crates:
        return {"valid": False, "reason": "malformed_board"}
    if not isinstance(actions, str):
        return {"valid": False, "reason": "actions_not_string"}
    player = actors[0]
    pushes = 0
    deltas = {"U": (0, -1), "R": (1, 0), "D": (0, 1), "L": (-1, 0)}
    for index, move in enumerate(actions):
        if move not in deltas:
            return {"valid": False, "reason": "unknown_move", "index": index}
        dx, dy = deltas[move]
        target = (player[0] + dx, player[1] + dy)
        if target not in traversable:
            return {"valid": False, "reason": "wall_or_outside", "index": index}
        if target in crates:
            beyond = (target[0] + dx, target[1] + dy)
            if beyond not in traversable or beyond in crates:
                return {"valid": False, "reason": "blocked_push", "index": index}
            crates.remove(target)
            crates.add(beyond)
            pushes += 1
        player = target
    return {"valid": crates == targets, "reason": "all_goals" if crates == targets else "incomplete",
            "moves": len(actions), "pushes": pushes, "final_crates": sorted(crates)}

