# Vast.ai Serverless

The venue-specific part of [running open-weight models](../open-weight-models.md)
for Vast.ai Serverless. It does not repeat Vast's own documentation; it records the
choices that make a Vast endpoint satisfy REPS and the operational facts learned
while running GovSim's open-weight models.

## Facts that shape the setup

- The proxy is `https://openai.vast.ai/<ENDPOINT_NAME>` with **no `/v1`**; the
  `/v1/...` form returns 404. Put the bare URL in the profile.
- The proxy ignores the request's `model` field: one endpoint serves one model.
  So one endpoint and one credential profile per model, `vllm.<key>`.
- The bearer token is a Vast API key. Two scopes are involved, and they differ:
  the key a **run** uses (stored in the `vllm.<key>` profile) only has to be accepted
  by the proxy; `misc` plus `user_read` and `instance_read` is enough and also lets a
  monitor list workers. The key that **manages** endpoints, workergroups and
  templates through the API needs `misc`, `user_write` (Vast files template
  creation under it, alongside API-key and SSH-key management), `user_read` and
  `instance_read`. Keep the manager key out of the profiles; the sweep never needs it.
- The proxy returns 504 for any request that takes longer than about a minute.
  The client retries 5xx, so slow models cost retries rather than runs; keep
  concurrency low enough that calls finish under a minute (see "Run").
- A worker reports Ready only after the template's benchmark finishes, a few
  minutes after vLLM logs "Application startup complete". Hosts fail and get
  replaced; budget 10 to 30 minutes from creation to Ready.
- Stopped workers keep their disks and bill storage. Delete the endpoint when a
  batch is done; stopping it is not enough.
- Gated Hugging Face repos need `HF_TOKEN` (a read token) in the account's
  environment variables, and the licence accepted on each repo page.

## Template

Clone the stock "vLLM (Serverless)" template, keep it private, and change two
environment variables:

- `MODEL_NAME` = the upstream repo id, exactly as it will appear after `vllm/`.
- `VLLM_ARGS` = `--download-dir /workspace/models --host 127.0.0.1 --port 18000`.
  The stock value carries Qwen3-specific reasoning and tool-call parsers; drop
  them for any other model. Tensor parallelism comes from the GPU count
  (`AUTO_PARALLEL=true`), so no `--tensor-parallel-size`.

Leave the port mapping and the `SERVERLESS`, `BACKEND`, `MODEL_SERVER_URL`,
`MODEL_HEALTH_ENDPOINT` and `MODEL_LOG` variables alone; they wire vLLM to the
worker process. The image does not count against the worker's disk allocation;
size the disk to the weights plus about 10 GB.

## GPU filter

Base line, then adjust the three memory and count fields per model:

```
direct_port_count>=2 compute_cap>=750 cuda_max_good>=12.9 gpu_total_ram>=24000 gpu_ram>=24000 inet_down>500 num_gpus in ['1']
```

| Weights | `gpu_ram` | `gpu_total_ram` | `num_gpus` |
|---|---|---|---|
| 7B to 8B bf16 | 24000 | 24000 | ['1'] |
| 13B bf16 | 48000 | 48000 | ['1'] |
| 70B class GPTQ 4-bit | 80000 | 80000 | ['1'] |
| Mixtral 8x7B bf16, 110B GPTQ | 80000 | 160000 | ['2'] |

For GPTQ models use `compute_cap>=800 compute_cap<=900` (Ampere to Hopper): the
fast 4-bit kernels are built for that range and Blackwell cards fall back to a slow
path. Restricting `num_gpus` matters because auto-parallel would otherwise shard a
small model across every card it is given.

## Endpoint

Advanced setup, not the one-click flow (its default keeps five workers). Minimum
Workers 0 always, Minimum Load 1, Max Workers 1 or 2, Inactivity Timeout 1800.
Minimum Load keeps one worker active while requests flow; Minimum Workers 0 plus the
timeout lets the endpoint scale to nothing when idle instead of parking a worker
whose disk bills storage. Name the endpoint after the model so a wrong profile is
obvious. Workers bill per hour, so a second worker only pays for itself if the
client keeps it as busy as the first: scale the sweep concurrency with the worker
count (four concurrent runs per worker, see "Run"). Two workers at eight runs finish
in about half the time for about the same money; two workers at four runs cost
nearly double. Vast adds workers on its own up to Max Workers when the endpoint is
saturated, so Max Workers is the real cost knob.

## Probe and profile

Run the probe from the main page with `VLLM_BASE_URL` set to
`https://openai.vast.ai/<ENDPOINT>` (no `/v1`) and `VLLM_API_KEY` to the Vast API
key, then store exactly those two values in `vllm.<key>`. A 504 from the probe
means no worker is Ready yet.

## Concurrency

Four concurrent runs per worker keeps calls under the proxy's 60-second cutoff for
70B-class models, so `--max-active` is four times Max Workers. Beyond that, calls
are retried rather than lost, but every retry throws away a full generation.

## Tear-down

Delete the endpoint as soon as the last run completes. Stopping is not enough: a
stopped worker keeps its disk and bills storage. Confirm the account's instance
list is empty afterwards.

## Doing it from the API

The console steps are three calls with the same Bearer key, then a poll:

1. `POST /api/v0/template` with the template fields above (`env` is the Docker
   flag string, `extra_filters` the GPU filter as JSON, `private: true`). Reading
   your own templates: `GET /api/v0/template/?select_filters={"creator_id":{"eq":<id>}}`.
2. `POST /api/v0/endptjobs` with `endpoint_name`, `min_load` 1, `target_util` 0.9,
   `cold_mult` 3, `cold_workers` 0 (Minimum Workers), `max_workers`. The create
   call ignores `inactivity_timeout`; set it with a `PUT` on the endpoint
   afterwards, which also accepts `cold_workers` and `max_workers`.
3. `POST /api/v0/workergroups` with `endpoint_id`, `template_hash`, the GPU
   filter as a JSON **dictionary** in `search_params` (the console-style string is
   mis-parsed: memory floors get scaled by 1000 and `in ['1']` keeps its quotes),
   and the same scaling values. Read it back and check `search_query` before a
   worker is recruited. A `PUT` on a workergroup must include `endpoint_id`; it
   did not change `cold_workers`, so delete and recreate if the query is wrong.
4. `GET /api/v1/instances/` until a worker is `running`, then the curl probe
   until it answers.
5. `DELETE /api/v0/endptjobs/{id}` when done; it removes the workergroup and its
   workers. Re-check the instance list.

Endpoint and workergroup objects returned by the API carry their own `api_key`
fields; never print or record them.
