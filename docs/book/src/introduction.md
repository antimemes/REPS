# What is ADB?

Agent Databank (ADB) packages agent experiments, runs them on your machine, and saves their inputs, results and execution records together. This repository provides the experiments, runner and Nix library.

## What would you like to do?

| I want to… | Start here |
| --- | --- |
| Run an experiment without cloning the repository | [Run your first local experiment](running/getting-started.md): inspect inputs, run a keyless example and read its result. |
| Run from a terminal or use a copied command | [Choose inputs and run](running/experiments.md#how-do-i-use-the-command-line): inspect the parameter schema, supply inputs and check the outcome. |
| Explore saved results | [Read the files directly](reference/local.md#how-do-i-read-the-files-without-a-browser). |
| Repeat a run or try different inputs | [Repeat and compare runs](running/model.md): keep the source and inputs, choose seeds and assess differences. |
| Add an experiment or change its code | [Add or change an experiment](authoring/experiments.md): clone ADB, edit, test locally and submit a pull request. |

See the [Roadmap](start/roadmap.md) for planned work on execution and recorded data.

## How are experiments and results organized?

For example, choosing a model and dataset limit sets inputs for an evaluation. Running that configuration twice saves two separate records. Changing the model lets you compare another configuration. A run's record can include messages, model requests and responses, scores, logs and files; the experiment determines what it emits.

An **experiment** defines a program and its inputs. A **condition** groups runs with the same experiment name, declared source content and complete set of input values. A **run** is one execution of a condition. Repeating a condition creates another run with its own ID and record.

For precise field and option definitions, use the references for [commands](reference/cli.md), [manifests](reference/manifest.md), [stored files](reference/layout.md) and [events](reference/events.md).
