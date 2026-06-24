import json


class ScenarioStore:
    """Loads and validates puppeteer scenarios (talking-point scripts)."""

    def __init__(self, scenarios):
        self._scenarios = list(scenarios)
        self._validate()

    def _validate(self):
        for s in self._scenarios:
            if not s.get("id") or not s.get("name"):
                raise ValueError(f"scenario missing id/name: {s!r}")
            injs = s.get("injections")
            if not isinstance(injs, list) or not injs:
                raise ValueError(f"scenario {s.get('id')!r} has no injections")
            for inj in injs:
                if not isinstance(inj.get("at_seconds"), (int, float)):
                    raise ValueError(f"injection at_seconds must be numeric: {inj!r}")
                if not isinstance(inj.get("text"), str) or not inj["text"].strip():
                    raise ValueError(f"injection text must be non-empty: {inj!r}")

    @classmethod
    def from_file(cls, path):
        with open(path, encoding="utf-8") as f:
            return cls(json.load(f))

    @property
    def scenarios(self):
        return self._scenarios
