"""Versioned execution inputs and append-only publication for safe recovery."""
from dataclasses import dataclass, field
import copy


@dataclass
class Result:
    name: str
    version: int
    payload: object
    inputs: dict = field(default_factory=dict)
    sources: dict = field(default_factory=dict)
    shared_reads: dict = field(default_factory=dict)
    citations: set = field(default_factory=set)
    complete: bool = True


class ExecutionStore:
    def __init__(self):
        self.results = {}
        self.source_versions = {}
        self.shared_versions = {}
        self.epoch = 0
        self.ledger = []
        self.invalid_versions = set()

    def invalid(self, changed=(), mode="actual"):
        invalid = set(changed) | {name for name, result in self.results.items()
                                  if (name, result.version) in self.invalid_versions}
        for name, result in self.results.items():
            if not result.complete:
                invalid.add(name)
            if any(self.source_versions.get(source) != version
                   for source, version in result.sources.items()):
                invalid.add(name)
            if mode == "actual" and any(self.shared_versions.get(key) != version
                                        for key, version in result.shared_reads.items()):
                invalid.add(name)
        while True:
            before = set(invalid)
            for name, result in self.results.items():
                parents = result.inputs if mode == "actual" else {
                    p: v for p, v in result.inputs.items() if p in result.citations}
                if any(parent in invalid or parent not in self.results
                       or self.results[parent].version != version
                       for parent, version in parents.items()):
                    invalid.add(name)
            if before == invalid:
                return invalid

    def invalidate(self, changed):
        self.epoch += 1
        invalid = self.invalid(changed)
        self.invalid_versions.update((name, self.results[name].version) for name in invalid
                                     if name in self.results)
        return invalid

    def commit(self, result, attempt_epoch):
        if attempt_epoch != self.epoch:
            raise ValueError("Obsolete attempt cannot commit")
        if (result.name, result.version) in self.invalid_versions:
            raise ValueError("Previously invalid result version")
        if result.name in self.results and result.version <= self.results[result.name].version:
            raise ValueError("Result versions must increase")
        for source, version in result.sources.items():
            if self.source_versions.get(source) != version:
                raise ValueError("Stale source version")
        for key, version in result.shared_reads.items():
            if self.shared_versions.get(key) != version:
                raise ValueError("Stale shared-state read")
        for parent, version in result.inputs.items():
            if parent not in self.results or self.results[parent].version != version:
                raise ValueError("Input version changed")
            if parent in self.invalid():
                raise ValueError("Invalid parent input")
        self.results[result.name] = copy.deepcopy(result)

    def publish(self, result_name, attempt_epoch, supersedes=None):
        if supersedes is not None and not any(e["id"] == supersedes and e["result"] == result_name for e in self.ledger):
            raise ValueError("Correction must reference an existing publication of this result")
        if attempt_epoch != self.epoch or result_name in self.invalid():
            raise ValueError("Invalid/obsolete publication")
        entry = {"id": len(self.ledger) + 1, "result": result_name,
                 "version": self.results[result_name].version,
                 "payload": copy.deepcopy(self.results[result_name].payload),
                 "epoch": attempt_epoch, "supersedes": supersedes}
        self.ledger.append(entry)
        return copy.deepcopy(entry)
