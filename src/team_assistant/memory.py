"""Scoped immutable policy versions; ordinary promotion differs from revocation."""
import copy
import hashlib
import json


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()


class PolicyMemory:
    def __init__(self):
        self.entries = {}
        self.default = {}
        self.runs = {}

    def promote(self, team, version, policy, validation):
        if version in self.entries:
            raise ValueError("Policy versions are immutable")
        if not validation.get("accepted") or validation.get("sha256") != digest(policy):
            raise ValueError("Gate failed or evaluated content changed")
        self.entries[version] = {"team": team, "policy": copy.deepcopy(policy), "valid": True}
        self.activate(team, version)

    def activate(self, team, version):
        entry = self.entries[version]
        if entry["team"] != team or not entry["valid"]:
            raise ValueError("Cannot activate out-of-scope/revoked policy")
        self.default[team] = version

    def pin(self, incident, team, store):
        if incident in self.runs:
            raise ValueError("Incident already pinned")
        version = self.default[team]
        entry = self.entries[version]
        if entry["team"] != team or not entry["valid"]:
            raise ValueError("Invalid policy scope/version")
        key = "policy:" + version
        store.shared_versions[key] = 1
        self.runs[incident] = {"version": version, "team": team, "store": store}
        return key, copy.deepcopy(entry["policy"])

    def rebind(self, incident):
        run = self.runs[incident]
        version = self.default[run["team"]]
        entry = self.entries[version]
        if not entry["valid"] or entry["team"] != run["team"]:
            raise ValueError("Invalid replacement policy")
        run["version"] = version
        key = "policy:" + version
        run["store"].shared_versions[key] = 1
        return key, copy.deepcopy(entry["policy"])

    def revoke(self, team, version):
        entry = self.entries[version]
        if entry["team"] != team:
            raise ValueError("Team cannot revoke another team's memory")
        entry["valid"] = False
        if self.default.get(team) == version:
            del self.default[team]
        affected = {}
        for incident, run in self.runs.items():
            if run["team"] == team and "policy:" + version in run["store"].shared_versions:
                store = run["store"]
                store.shared_versions["policy:" + version] = 0
                affected[incident] = sorted(store.invalidate([]))
        return affected

