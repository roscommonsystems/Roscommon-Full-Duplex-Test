import json


class ModelRegistry:
    """Loads and validates the model list from models.json."""

    def __init__(self, models):
        self._models = list(models)
        self._by_id = {m["id"]: m for m in self._models if "id" in m}

    @classmethod
    def from_file(cls, path):
        with open(path, encoding="utf-8") as f:
            return cls(json.load(f))

    @property
    def models(self):
        return self._models

    def has(self, repo):
        return repo in self._by_id

    def get(self, repo):
        """Return the full model entry dict for a repo id, or None."""
        return self._by_id.get(repo)

    def display_name(self, repo):
        entry = self._by_id.get(repo)
        if entry and entry.get("name"):
            return entry["name"]
        return repo
