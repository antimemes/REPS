---
rfc: 4
title: Identity, provenance and pooling
status: draft
---

# RFC 0004: Identity, provenance and pooling

A condition groups runs of the same experiment with the same declared source content and bound params. Source identity covers the experiment's declared files and imported shared libraries; each run records the realized execution environment separately. Release and behavior labels identify commits and make claims about behavior without changing record identity, pooling keys or [schema compatibility](0002-schema-versioning.md).

## 0. Declared content is identity

`source` fingerprints the experiment's declared source paths: `package.nix`, `pyproject.toml`, `uv.lock`, patches and adapter code, plus the shared Python library source directories its program imports. `mkExperiment.sharedSrcs` defaults to `lib/reps-events/reps_events` and `lib/reps-experiment/reps_experiment`; Inspect programs also include `lib/reps-inspect/reps_inspect`. `cleanImport` excludes development artifacts, and surrounding tests, READMEs and tooling stay outside the library source paths. `nixpkgs`, `uv2nix`, the runner's own closure, tests, READMEs, web, docs and unrelated scripts are outside identity. A GovSim adapter comment changes GovSim's fingerprint without changing Concordia's; an `reps_events/emit.py` change affects both. The experiment's dependency versions in `uv.lock` and explicit interpreter selection in `package.nix` are declared source, not implicit build environment.

If a glibc CVE re-fingerprinted every experiment, conditions would re-split every few weeks and stop being a usable pooling unit. Excluding the platform toolchain is a pooling decision, not a claim that it cannot affect behavior. `uv.lock` is hashed without `[package.metadata.requires-dev]` tables: repeated path-dependency dev metadata is outside identity, so shared test dependency changes do not split experiment conditions. The exact realized experiment closure is recorded per run in `run.start.runtime.experiment_bin` ([§3](#3-execution-snapshot)), copied to `provenance.runtime.experiment_bin` on the card; nothing is lost from the closure's recorded identity. Reproducing that closure still requires retaining its inputs or store objects. Runs can be compared by closure without assigning them new conditions. This fingerprint identifies declared content, not all external influences on an execution. The [identity note](../book/src/running/model.md) describes its use when comparing runs.

## 1. The interpreter rule

Every Python experiment declares its own interpreter in `package.nix`. `mkPythonEnv` has no default for `python`: omitting it fails the build. A Python experiment passes its chosen interpreter explicitly; the declaration selects its interpreter and, because `package.nix` is an identity path, records that choice in its source identity. The declaration and the exact realized closure are distinct: a maintenance update to the selected interpreter can change the closure while the declaration stays the same.

The platform cannot own this choice. `mkExperiment.program` is language-agnostic: any program that speaks the runner protocol can be an experiment. A generic `experiment_python_version` field would be meaningless for a non-Python experiment. The experiment owns its toolchain.

## 2. Fetch reference and tree hash

`fetch_ref: str | None` identifies a pinned clean repository revision from which the packaging source can be fetched. A dirty tree has no `fetch_ref`; neither does a launch with no known pinned clean revision. None is omitted on the wire. The launcher rejects userinfo before exporting a reference. A source fingerprint or packaging hash cannot stand in for a fetchable revision, and a saved reference does not by itself guarantee continued availability of the revision.

`tree_hash: str | None` is the packaging tree's NAR hash. The launcher emits it whenever known, for clean and dirty trees alike. It identifies that packaging tree independently of the narrower experiment `source` fingerprint and does not participate in condition identity. It is evidence of content, not a storage location: recovering a dirty tree still requires preserving its bytes. `tree_hash` is recorded only where the launcher can compute it, such as flake builds; its absence carries no meaning.

Sweeps refuse to launch from a dirty tree. Publishing refuses a card with no `fetch_ref`: a run with no commit belongs to no release, ever. Local exploratory runs remain possible, but the absence of a pinned revision cannot be repaired by assigning a release label afterward.

## 3. Execution snapshot

The runner's closure never determines a record's versions: the experiment manifest declares `v` and `schema`, and the runner stamps those declarations after checking vocabulary compatibility.

`run.start` carries launch facts and `run.end` carries terminal process facts; neither contains aggregates of other records. The stream is the truth, the card is a cache of it, and nothing reads the card as an input. Each launcher invocation executes one run. Runs record the `--seed` argument unchanged, or a random non-negative 31-bit seed when omitted. The sections of the runner's index card, local storage and published layout are defined in [RFC 0003](0003-run-directory-and-published-layout.md). Runtime closure paths can differ between the flake and classic doors on a checkout with untracked files while `source` does not: `source` is identity, and closure paths are covariates.

The runner pins both `LANG` and `LC_ALL` to `C.UTF-8` in the child environment, rather than inheriting the launching shell's locale. It records the CPU model and core count in `run.start.runtime`, which the card copies into its provenance section. These are execution covariates, never identity. The producer's own toolchain is recorded in `producer.python`.

## 4. Condition as pooling key

`condition = hex(sha256(JCS({experiment, source, params})))[:40]` is the pooling key for runs with the same declared experiment, fingerprint and bound input values. This is 160 bits, matching the Nix store's hash width, encoded as 40 lowercase hexadecimal characters. `canonical.condition_id` is the single implementation; records and paths use its result unchanged. Condition storage names append `-<experiment>`; readers derive those names from the recorded fields and never parse the suffix. Each execution has its own run identifier, `yyyymmddthhmmssz-<12 lowercase hex random>`, using the launching machine's UTC clock and six random bytes. The whole string is the ID; its date is a launch-time label, not evidence. Seeds, credential profiles, endpoints, runner/platform details, fetch references and packaging tree hashes are outside this key. Pooling establishes matching declared inputs; it does not assert equivalent provider behavior or scientific comparability.

## 5. Toolchain bumps and the claim they make

`nixpkgs` stable and `uv2nix` are bumped together at each six-month stable release. Security patches within a stable release are taken as they land. A platform toolchain bump asserts that experiment behavior is unchanged. That is a falsifiable, withdrawable claim, not a proof. Replicates straddling a bump share a `condition_id` and differ in `experiment_bin`; the recorded closure provides the distinction researchers use to test the claim. The claim is tested against the producer's interpreter, libc and locale recorded in `producer.python`.

Re-locking `uv.lock` is not a platform toolchain bump. It changes declared experiment content and is expected to change identity. Changing an explicit interpreter declaration likewise changes source identity. Neither change is hidden behind the behavior-preservation claim for excluded build inputs.

The same withdrawable claim applies to any commit labelled behavior-preserving.

## 6. Releases and version labels

A release names a commit. Its membership is the published runs whose `fetch_ref` points to that commit, without an enumerated membership list. Release labels are applied after the fact and exist to select a version to run. They are never written into records and never change a run's fingerprint, condition or schema. Pooling follows condition identity (experiment, fingerprint and bound params), never a release label or version number.

An experiment behavior version, such as `0.1.1`, is independent of schema compatibility. A behavior change can change the source fingerprint while using the same schema, and a compatible schema addition does not prescribe a behavior release. Behavior labels are never coupled to schema integers. Versions are chronological and never renumbered. A behavior break is recorded against the fingerprints it separates in an explicit catalog field, without rewriting the identity of existing runs.

Claims are withdrawn in place. Git history is the erratum: it preserves the original claim and the correction. Withdrawing a claim neither rewrites run records nor retrospectively changes their fingerprints.
