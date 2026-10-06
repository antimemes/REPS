# Native GovSim log

`log_env.json` is the complete, unchanged upstream log from REPS run
`01M2K0ZJ4VF2ECAYPZ8T6Y4S14`: pinned GovSim `1d11adf047b24fa2ba0d44a1d4931015ea2e5210`,
baseline fishing, one round, `openai/gpt-5-nano`, hash embedder, base seed 42.
The run directory passed the credential scan. All four actions written by the
pinned `concurrent_env.py` are present, including native HTML and explicit nulls.

`mock_storage/` contains the complete, unchanged `log_env.json` and all five
`persona_*/nodes.json` files from mock run
`01M2NCGCG00QHKZKM2AP6DWHNE`: the same pinned upstream, baseline fishing, two
rounds, `mock/model`, hash embedder, base seed 37. The arrays retain native node
fields and explicit nulls. This fixture checks file boundaries, hashes, counts,
and every memory hint against actual upstream output. Embedding files are omitted
because node descriptions and the hash embedder reproduce them.
