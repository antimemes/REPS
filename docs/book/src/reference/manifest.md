# Experiment manifest

A manifest is generated JSON describing an experiment's inputs and presentation. The runner uses it for validation, credential discovery and result descriptions.

Read a packaged manifest with:

```sh
nix run .#inspect-hello -- --describe
```

## Top-level fields

| Field | Meaning |
| --- | --- |
| `schema_version` | Currently `1`; results are ordered, named declarations. |
| `name` | Unique experiment name and app name. |
| `summary` | Short description of the experiment. |
| `readme` | Optional Markdown source from the experiment directory’s `README.md`. |
| `params` | Object mapping parameter names to declarations. Every key must be bound for a run. |
| `schema` | Payload union identity and export: `{ "version": 0, "models": "module:attribute", "path": "/nix/store/.../schema.json" }`. Separate from the manifest file's `schema_version`. |
| `results` | Ordered list of possible result declarations (see below), each with a unique `name`. The runner warns on repeated or undeclared result events; readers derive the last emitted value for each declared name. |
| `env` | Optional experiment metadata. It does not cause the runner to inject arbitrary variables or provision services. |
| `origin` | Packaging repository reference, separate from a run's pinned fetch reference. |
| `links` | External references, each with `label` and `url`. |

The source identity and executable are wrapper settings, not manifest fields. [`adb.mkExperiment`](../authoring/experiments.md) takes `name`, `summary`, `params`, `program` and `src`, with optional `results`, `schema`, `schemaPython`, `sharedSrcs`, `env` and `links`. Schema defaults to version 0 and `adb_events:Payload`; typed experiments supply their own union pointer and `schemaPython = "${env}/bin/python"` from the built program environment. Packaging imports that pointer and calls `export_schema`, failing the build if it cannot import. `schema.json` and the shared export sit beside the manifest, along with a `python` link to the schema interpreter used by `adb-runner verify`; `schema.path` is generated, not author-supplied.

The catalog packages the experiment directory's images under
`assets/<experiment-name>/`, preserving subdirectories such as `figures/`. Relative
image paths in `readme` refer to these assets, copied to
`index/catalog/assets/<name>/` in the published site. Images are separate
catalog assets, not embedded in the manifest JSON.

## Parameter declarations

Each entry of `params` contains `type` plus optional fields:

| Field | Meaning |
| --- | --- |
| `initial` | Initial form value and CLI suggestion. It is never an implicit run-time parameter default. |
| `description` | Explanation shown alongside the input. |
| `nullable` | Permit explicit JSON null; default false. |
| `order` | Presentation order, lower first; default `100`, with name breaking ties. |
| `group` | Form section label. |
| `suggestions` | Suggested strings or `{ "value": STRING, "description": STRING }` entries. Suggestions do not restrict legal values. |
| `minLen`, `maxLen` | List-length bounds checked before execution on realized values. |
| `fields` | Named parameter declarations for a structured object editor. The runner still validates an `object` as free-form JSON. |
| `depends_on`, `variants` | Form metadata selecting a field set using another parameter's value. No in-tree manifest currently produces this variant form. |

LLM-typed parameters receive shared model suggestions during manifest generation. This also applies to LLM fields inside lists of structures. Experiment-specific suggestions are prepended to the shared suggestions.

## Result declarations

`results` is an ordered list of declarations. Each declaration contains a unique name, a type descriptor and optional presentation fields:

| Field | Meaning |
| --- | --- |
| `name` | Required string matching the `name` on a result event. Duplicate names fail Nix evaluation. |
| `type` | Required type descriptor, using the types listed below. It does not enforce the emitted result's type. |
| `label` | Optional string used as the readable result name. The metric key remains its identifier. |
| `description` | Optional short string explaining the result in plain language. |
| `details` | Optional longer string explaining the calculation, aggregation or interpretation caveats. |
| `unit` | Optional string displayed alongside the value; it does not convert or rescale the value. |

In Nix, use `results = [ { name = "count"; type = adb.types.int; label = "Recorded count"; description = "Count supplied to the program."; } ];`. The manifest preserves the list and its presentation fields in that order. Map declarations and bare-type shorthand are not supported.

Results are possible outputs, not required outputs. A declared metric that was never emitted is absent from the summary; absence does not mean zero. Undeclared results remain in the stream but warn and stay out of the summary. Boolean results do not inherently indicate success or failure; their meaning comes from the declaration.

The runner snapshots the ordered declaration list in `run.json` under `definitions.results` and in `run.start` as `result_definitions`. These saved definitions preserve the run's interpretation when the installed experiment changes. Result events have no unit field; units come from the declaration. Events do not override result labels, descriptions or details.

## Type descriptors

| Nix constructor | JSON descriptor | Accepted value |
| --- | --- | --- |
| `adb.types.str` | `{ "kind": "str" }` | String |
| `adb.types.llm` | `{ "kind": "llm" }` | Model-ID string; also participates in credential discovery |
| `adb.types.int` | `{ "kind": "int" }` | Integer, excluding booleans |
| `adb.types.float` | `{ "kind": "float" }` | JSON number, excluding booleans |
| `adb.types.bool` | `{ "kind": "bool" }` | Boolean |
| `adb.types.enum values` | `{ "kind": "enum", "values": [...] }` | One declared value |
| `adb.types.listOf type` | `{ "kind": "list", "of": TYPE }` | Array whose elements match `TYPE` |
| `adb.types.struct fields` | `{ "kind": "struct", "fields": {...} }` | Object with exactly the declared keys and matching field types |
| `adb.types.object` | `{ "kind": "object" }` | JSON object with arbitrary keys and values |

Use `listOf (struct {...})` for a list of structured values. A structure field can be a bare type descriptor or a wrapper containing `type`, `description` and `suggestions`. `param type attrs` attaches parameter metadata in Nix.

For example:

```nix
with adb.types; {
  agents = param (listOf (struct {
    name = str;
    model = param llm { description = "Model for this agent."; };
  })) {
    minLen = 1;
    description = "One row per agent.";
  };
}
```

## What does validation establish?

The runner rejects unknown and missing parameter keys, then checks value types before hashing the condition. It checks realized values again before executing, including list-length bounds. Specified and realized parameters are currently identical; no distribution or sweep syntax is implemented.

Validation does not verify model existence, credentials, endpoint access or semantic constraints that only the experiment understands. Result type descriptors do not validate emitted metrics against the result declaration; standard result payloads have their own [scalar-value rule](events.md#results).
