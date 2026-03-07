class WallTracker:
    def __init__(self):
        self.last_wall = None

    def check_wall(self, wall_size):

        result = {
            "wall_weakening": False,
            "wall_removed": False
        }

        if self.last_wall is None:
            self.last_wall = wall_size
            return result

        if wall_size < self.last_wall * 0.7:
            result["wall_weakening"] = True

        if wall_size < self.last_wall * 0.3:
            result["wall_removed"] = True

        self.last_wall = wall_size

        return result
