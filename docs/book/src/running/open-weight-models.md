# Run open-weight models

REPS talks to a self-hosted model the same way it talks to a hosted one: an
OpenAI-compatible chat endpoint, selected by a credential profile, with the model
ID naming the serving engine and the upstream repo
(`vllm/Qwen/Qwen1.5-72B-Chat-GPTQ-Int4`, see [model IDs](model.md)). Where the
engine runs is a venue detail that lives in the profile, never in the model ID or
the records. This page is the venue-independent part; each venue has its own
subpage with the choices that make its endpoint satisfy REPS:

- [Vast.ai Serverless](open-weight-models/vast.md)

## What REPS needs from the endpoint

- A chat-completions route at one stable URL. The client appends
  `/chat/completions` to the profile's `VLLM_BASE_URL`.
- The response's `model` field must equal the repo id after `vllm/`; the first
  call of every run checks this and fails the run on a mismatch.
- A trailing assistant message is continued as a prefill. The client sends vLLM's
  `continue_final_message` for `vllm/` ids; nothing to configure server-side.
- vLLM rejects a request whose prompt plus `max_tokens` exceeds the model's
  context. For models with contexts below the experiment's token budget, set the
  budget to `null` where the experiment allows it (GovSim does; null omits it).

## Profile

One credential profile per endpoint:

```sh
nix run .#reps-runner -- credentials set vllm.<key>
```

`VLLM_BASE_URL` is the URL the server mounts the OpenAI API under; the client
appends `/chat/completions` itself. `VLLM_API_KEY` is whatever bearer the venue
expects, and may be empty for a server that ignores auth. Profile names allow
`[a-z0-9_-]` only, so `qwen15-72b`, not `qwen1.5-72b`. For scripting, the `--json`
form reads `{"VLLM_API_KEY": ..., "VLLM_BASE_URL": ...}` from stdin. A run selects
the profile with `--credential vllm=<key>` and the model with
`--set model=vllm/<REPO>`.

## Prove it before running

```sh
curl "$VLLM_BASE_URL/chat/completions" \
  -H "Authorization: Bearer $VLLM_API_KEY" -H "Content-Type: application/json" \
  -d '{"model":"<REPO>","messages":[{"role":"user","content":"Pick a number from 1 to 10."},{"role":"assistant","content":"Answer:"}],"add_generation_prompt":false,"continue_final_message":true,"temperature":0,"max_tokens":20}'
```

Two checks: `model` echoes the repo id, and the content continues after "Answer:"
rather than restating it. Either failing means the first run would fail the same
way, so fix it before anything bills.

## Run

Match sweep concurrency to what the server can answer within any proxy or
gateway timeout in front of it; the venue pages give numbers. One window per
replicate (`--split-replicates`) avoids a serial tail at the end of a batch. Wait
until the endpoint answers the probe before launching: a cold endpoint fails the
first runs, which then need re-queueing. The sweep refuses to launch from a tree
with modified tracked files, so commit or stash edits (a docs page in progress
counts) before the endpoint is up and billing.

## Finish

Audit the batch from its records (errors, retries, served model, counts per
cell), then publish. If a batch was killed and relaunched, the killed cards stay
`running` and the publisher reports them as not terminal; publish by run id
(`<stem>/<run>`) instead of by stem. The publisher resolves the manifest catalog
once per run with `nix-build -A manifests`, about seven seconds of evaluation each,
so a 75-run batch spends ten minutes on that alone; build the catalog once and
point `REPS_MANIFESTS` at it for the dry-run and the publish. Tear the venue down
as soon as the last run completes; publishing reads only local records.
