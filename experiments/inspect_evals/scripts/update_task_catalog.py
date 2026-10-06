#!/usr/bin/env python3
"""Regenerate task_catalog.json — the generated list of this family's experiments.

One command, no hand edits:  task tasks:update  (runs `uv run python scripts/...`
here — the script imports the PINNED inspect_evals, so it must run in this
project's env, and the catalog it writes is exactly what that pin registers).

Two views of the same package are joined:
  - the inspect registry ....... ground truth: every @task actually registered
                                 and importable at this pin
  - load_listing() ............. upstream's per-eval declared metadata
                                 (description, group, tags, dependency extra,
                                 sandbox, requires_internet)

package.nix maps the catalog to one experiment per task. Tasks whose eval
declares a `dependency`/`dependency_group` (a pip extra we don't install) stay
in the catalog with that field set and get NO experiment — degraded but
correct; installing the extra (pyproject edit + relock + regen) enables them.
Sandbox/internet needs are copied from the declared runtime metadata, never
guessed.

Each task's @task kwargs become real typed params — there is NO task_args blob
and no unset state: a kwarg with a concrete JSON-able default flattens to a
required param whose `initial` is that default; a kwarg whose declared default
is None becomes a required NULLABLE param with `initial: null` (`--set k=null`
— the upstream default encoded directly, so an upstream default change can
never silently reinterpret an invocation). A kwarg shadowing a family param
name (seed, limit, epochs, ...) is prefixed `task_` (the kwarg map records its
real name for the adapter). Kwargs that can't be JSON (Solver, Scorer, ...)
are excluded and listed. Descriptions come from the docstring's Args section.
Output is deterministic (sorted, no timestamps): a clean `git diff` after
running IS the update.
"""

from __future__ import annotations

import inspect as pyinspect
import json
import re
import sys
import types
import typing
from pathlib import Path

# names the family's own params own; a task kwarg with one of these names is
# still reachable, but only through the task_args sub-form (where it
# unambiguously means the TASK's kwarg)
FAMILY_PARAMS = {"model", "limit", "epochs", "task_args", "generate_args", "seed"}

SCALAR = {str: "str", int: "int", float: "float", bool: "bool"}


def map_type(ann) -> dict | None:
    """annotation -> reps type descriptor, or None when not representable."""
    args = [a for a in typing.get_args(ann) if a is not type(None)]
    if typing.get_origin(ann) in (typing.Union, types.UnionType):
        if len(args) == 1:
            return map_type(args[0])
        # `str | list[str]` (split/subset selectors): render as str, one value
        if str in args and len(args) == 2:
            other = next(a for a in args if a is not str)
            if typing.get_origin(other) is list:
                return {"kind": "str"}
        return None
    if ann in SCALAR:
        return {"kind": SCALAR[ann]}
    if typing.get_origin(ann) is typing.Literal:
        values = typing.get_args(ann)
        if all(isinstance(v, str) for v in values):
            return {"kind": "enum", "values": list(values)}
    if typing.get_origin(ann) is list:
        of = map_type(typing.get_args(ann)[0])
        if of:
            return {"kind": "list", "of": of}
    return None


def arg_docs(fn) -> dict[str, str]:
    """Google-style `Args:` section of the docstring -> {kwarg: description}."""
    docs, current = {}, None
    lines = (fn.__doc__ or "").splitlines()
    in_args = False
    for line in lines:
        if re.match(r"\s*(Args|Arguments|Params|Parameters)\s*:\s*$", line):
            in_args = True
            continue
        if in_args:
            if re.match(r"\s*(Returns|Raises|Yields|Examples?|Notes?)\s*:\s*$", line) or not line.strip():
                if not line.strip() and current is None:
                    continue
                if line.strip():
                    break
                continue
            m = re.match(r"\s{2,}(\w+)\s*(?:\([^)]*\))?\s*:\s*(.*)", line)
            if m and not line.startswith(" " * 8):
                current = m[1]
                docs[current] = m[2].strip()
            elif current:
                docs[current] = (docs[current] + " " + line.strip()).strip()
    return docs


def task_params(fn) -> tuple[dict, dict, list[str]]:
    """(params, param-name -> kwarg-name map, excluded kwarg names)."""
    params, kwargs_of, excluded = {}, {}, []
    docs = arg_docs(fn)
    sig = list(pyinspect.signature(fn).parameters.values())
    kwarg_names = {p.name for p in sig}
    for position, p in enumerate(sig):
        if p.kind in (p.VAR_KEYWORD, p.VAR_POSITIONAL):
            continue
        default = p.default
        t = map_type(p.annotation)
        if t is None and default is not pyinspect.Parameter.empty and type(default) in SCALAR:
            t = {"kind": SCALAR[type(default)]}  # unannotated: the default declares the type
        if t is None or default is pyinspect.Parameter.empty:
            excluded.append(p.name)
            continue
        name = p.name
        if name in FAMILY_PARAMS:
            name = f"task_{name}"
            if name in kwarg_names:
                raise SystemExit(f"{fn.__name__}: rename collision on {name}")
        # presentation order = the signature's declared order: task authors put the
        # treatment knobs first and the plumbing (sandbox/hardware overrides) last
        entry = {"type": t, "description": docs.get(p.name, ""), "order": position}
        if default is None:
            entry["nullable"] = True
            entry["initial"] = None
        else:
            entry["initial"] = list(default) if isinstance(default, tuple) else default
        params[name] = entry
        kwargs_of[name] = p.name
    return params, kwargs_of, sorted(excluded)


def eval_links(ev, tag: str) -> list[dict]:
    """Upstream's declared link metadata -> [{label, url}] for the experiment page.

    `arxiv` is upstream's reference-URL field (not always arxiv); `path` is the
    eval's directory in the upstream repo, linked at the PINNED tag so the docs
    match what actually runs; huggingface external assets carry the dataset id.
    direct_url/git_clone assets are fetch templates (`{SHA}` placeholders), not
    browsable pages — skipped, they are captured by upstream's pinning, not links.
    """
    links = []
    if ev.arxiv:
        links.append({"label": "paper", "url": str(ev.arxiv)})
    if ev.path:
        links.append({
            "label": "source",
            "url": f"https://github.com/UKGovernmentBEIS/inspect_evals/tree/{tag}/{ev.path}",
        })
    seen = set()
    for asset in ev.external_assets or []:
        if asset.type.value == "huggingface" and asset.source not in seen:
            seen.add(asset.source)
            links.append({
                "label": asset.source,
                "url": f"https://huggingface.co/datasets/{asset.source}",
            })
    return links


def first_sentence(text: str) -> str:
    line = " ".join((text or "").strip().split())
    for stop in (". ", "? ", "! "):
        if stop in line:
            return line.split(stop)[0] + stop.strip()
    return line


def main() -> int:
    import importlib.metadata

    from inspect_ai._util.registry import registry_find, registry_info
    from inspect_evals.metadata import load_listing

    # identity fallback for registry-only tasks (no eval.yaml): the package version —
    # conservative, they re-version on every release
    pkg_version = f"inspect-evals-{importlib.metadata.version('inspect-evals')}"
    # upstream tags releases vX.Y.Z — source links point at the pinned tag
    pin_tag = f"v{importlib.metadata.version('inspect-evals')}"

    registered = {
        registry_info(t).name.removeprefix("inspect_evals/"): t
        for t in registry_find(lambda i: i.type == "task")
    }

    tasks = {}
    for ev in load_listing().evals:
        rt = ev.runtime_metadata
        for t in ev.tasks:
            if t.name not in registered:
                continue
            params, kwargs_of, excluded = task_params(registered[t.name])
            tasks[t.name] = {
                "task": f"inspect_evals/{t.name}",
                "summary": f"{ev.title}: {first_sentence(ev.description)}",
                # Upstream's declared comparability version: regeneration prints
                # changes for review; this is not the REPS source identity.
                "version": ev.version.full_version,
                "params": params,
                "param_kwargs": kwargs_of,
                "excluded_kwargs": excluded,
                "group": ev.group,
                "tags": ev.tags or [],
                # the pip extra (or dependency group) the eval declares; non-null
                # means package.nix generates no experiment for it
                "dependency": ev.dependency or ev.dependency_group,
                "sandbox": bool(rt and rt.sandbox),
                "requires_internet": bool(rt.requires_internet) if rt and rt.requires_internet is not None else True,
                "dataset_samples": t.dataset_samples,
                "links": eval_links(ev, pin_tag),
            }
    # registered but absent from the listing: still real, still runnable — carry
    # them with docstring summaries so the catalog never silently drops a task
    for name, fn in registered.items():
        if name not in tasks:
            params, kwargs_of, excluded = task_params(fn)
            tasks[name] = {
                "task": f"inspect_evals/{name}",
                "summary": first_sentence(fn.__doc__ or name),
                "version": pkg_version,
                "params": params,
                "param_kwargs": kwargs_of,
                "excluded_kwargs": excluded,
                "group": None,
                "tags": [],
                "dependency": None,
                "sandbox": False,
                "requires_internet": True,
                "dataset_samples": None,
                "links": [],
            }

    out = {
        "_generated_by": "scripts/update_task_catalog.py — do not edit by hand",
        "tasks": dict(sorted(tasks.items())),
    }
    path = Path(__file__).resolve().parent.parent / "task_catalog.json"
    # comparability diff vs the previous catalog — the advisory seed: identity
    # deliberately over-fragments on any pin bump, and read-time pooling re-unifies
    # by default; each DECLARED version bump printed here is a candidate for a
    # split advisory scoped to that eval (a release note, not an identity input)
    previous = json.loads(path.read_text())["tasks"] if path.exists() else {}
    for name, t in tasks.items():
        old = previous.get(name, {}).get("version")
        if old is not None and old != t["version"]:
            print(f"comparability: {name} {old} -> {t['version']} — split-advisory candidate")
    for name in sorted(set(previous) - set(tasks)):
        print(f"comparability: {name} removed upstream")
    path.write_text(json.dumps(out, indent=1) + "\n")
    runnable = sum(1 for t in tasks.values() if not t["dependency"])
    print(
        f"wrote {path.name}: {len(tasks)} tasks "
        f"({runnable} runnable, {len(tasks) - runnable} needing an uninstalled extra)"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
