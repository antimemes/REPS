# Publishing

ADB publishes verified terminal runs to an S3 bucket at a target supplied by you.
There are no built-in hosts, provider presets, saved remotes, or target
environment variables. The bucket contains only `runs/` under the selected prefix.

## Set up an AWS profile

Create the bucket and configure a named AWS profile using your storage provider's
instructions. For Hugging Face Storage Buckets, follow its
[S3 client and AWS profile setup](https://huggingface.co/docs/hub/storage-buckets-s3).
For Amazon S3, follow the
[AWS credentials guide](https://docs.aws.amazon.com/boto3/latest/guide/credentials.html).
Keep endpoint, region, addressing and checksum settings in your AWS configuration,
and credentials in the provider's supported credential source.

`--profile research` selects that AWS profile. Omit it to use
[boto3's normal credential resolution](https://docs.aws.amazon.com/boto3/latest/guide/credentials.html).
ADB creates a boto3 session and its S3 client without overriding endpoint, region
or client configuration. Ambient AWS settings are not passed into the experiment
process; credential sets explicitly selected for the experiment still are.
The web Publish panel reads profile names from `~/.aws/config` and never writes
AWS files.

## Publish when a run finishes

Add `--publish` and optionally an AWS `--profile` to the experiment command. This
complete mock-model example executes one run and publishes it:

```bash
nix run .#inspect-hello -- --set model=mockllm/model --set limit=0 --set epochs=1 --set 'generate_args={}' --publish s3://my-bucket/adb-v1 --profile research
```

The same options are available in the browser's **Publish** panel: enter the
S3 target, choose a profile or default AWS resolution, and enable **Publish on
completion**. The copied command and the browser Run button use those settings.
The run's data directory still follows `--data-dir`, `ADB_DATA_DIR`, then the
[XDG default](../reference/local.md#choose-where-results-are-saved).

`--profile NAME` selects the AWS profile for publication. Model credentials
use `--credential SET=NAME`; both flags can appear in the same command.

Publication requires a terminal state, a pinned clean `provenance.fetch_ref`, and
a passing [verify audit](model.md#how-do-i-audit-the-first-real-run).
An unpinned run is refused, including with `--dry-run`; a later release label
cannot supply its missing revision.
Failed and interrupted runs can publish when their evidence passes that audit.
A publishing error is logged without changing the run state or exit code.

## Publish saved runs

Run the batch command against a local data directory:

```bash
nix run .#adb-runner -- publish --to s3://my-bucket/adb-v1 --profile research --data-dir "$HOME/adb-data"
```

With no stems, it considers all runs. Add `--experiment NAME` to filter by the
experiment recorded on each card, or supply one or more positional stems:
`<condition>-<experiment>` for a whole condition, or
`<condition>-<experiment>/<run>` for one run. Stems are relative to `runs/`.
Each run is verified independently; a rejected run does not stop the batch.
The command returns a nonzero exit code if any run fails to publish.

`--dry-run` verifies and prints the exact object keys and byte sizes, including
compressed streams. It checks the bucket for existing run keys but writes nothing.
Verification resolves the manifest in the same way as `adb-runner verify`: from
`ADB_MANIFEST`, then the experiment's file in `ADB_MANIFESTS`, or a manifest catalog
built from the current checkout.

Publication checks both destination run keys with HEAD. If either already exists,
it refuses to overwrite that run, including a partial upload. Other runs continue.
Choose a new prefix when you need different immutable objects.
The publisher uses only `HeadObject` and `PutObject`: it uploads the stream
first and the card last.

Each run uploads exactly these two objects:

```text
<prefix>/runs/<condition>-<experiment>/<run>/events.jsonl.zst
<prefix>/runs/<condition>-<experiment>/<run>/run.json
```

The stream is compressed at zstd level 19 in a single frame with
`Content-Type: application/zstd`, preserving its original bytes when decompressed.
The card is copied byte-for-byte with `Content-Type: application/json`.
Workspaces are never uploaded. See the [published layout](../reference/layout.md#published-runs).

## Build and serve an index

Experiment buckets contain only `runs/`. A separate, replaceable index lets a
static browser discover those runs. Keep `stores.yaml` and `site.json` in the
site repository. Write the store list yourself; ADB does not infer a public URL
from an S3 address. Store lists accept YAML (`.yaml` or `.yml`) or JSON.
`data.example.org` below is an example store hostname.

```yaml
v: 0
stores:
  - s3: s3://my-experiment-bucket/adb-v1
    profile: research
    url: https://data.example.org/experiments/adb-v1
    experiments:
      - govsim
    exclude:
      - runs:
          - <run-id>
        reason: Provider outage invalidated these results.
```

`url` must expose the same prefix over public HTTPS. Optional `experiments`,
`conditions` and `runs` lists filter exact recorded values and intersect.
Leave a filter out to include everything; an empty list includes nothing.

An optional `exclude` list removes runs even when the inclusion filters select them.
Each entry requires a non-empty list of run IDs and a non-empty reason, because the
store list is the site operator's record of what it chose not to show. A run ID can
appear in only one exclusion entry per store. Exclusions are logged with their
reasons on every rebuild, including `--dry-run`; unknown IDs warn without failing
the build. Exclusions change only the site's index and never touch bucket objects.

Store profiles select source credentials. The index command writes to a local
directory that the site deploys.
The index reads only `ListObjectsV2` and `GetObject`, using each store's profile
without endpoint, region or client-configuration overrides.

Run cards are cached under `$XDG_CACHE_HOME/adb/index` (or `~/.cache/adb/index`),
keyed by profile (`default` when omitted), bucket, full object key and the listed
ETag. Every rebuild still lists the store, so the listing determines membership
and changed ETags trigger a fetch.
The cache does not affect the index output; `built_at` still records each rebuild's
time. Use `--cache-dir DIR` to choose another cache directory or `--no-cache` to bypass
cache reads and writes. `--dry-run` can read existing entries but never writes them.
Store summaries report cached and fetched cards before filters and exclusions.

The complete `site.json` is:

```json
{"v": 0}
```

Unknown keys are rejected. CI builds the dist, copies it and `site.json` into
`site/`, generates `site/index`, and deploys `site/`. With the ADB Nix packages
and `adb-runner` available in CI:

```bash
web_dist="$(nix build .#adb-web-dist --no-link --print-out-paths)"
catalog="$(nix build .#manifests --no-link --print-out-paths)"
mkdir -p site
cp -R "$web_dist"/. site/
chmod -R u+w site
cp site.json site/site.json
adb-runner index --stores stores.yaml --catalog "$catalog" --to site/index
```

Deploy the resulting `site/` directory. Only CI uses AWS profiles; the browser
reads the generated index and public run objects.

`--catalog DIR` is required and points to the built manifest directory:
`<name>.json` files and optional `assets/<name>/` trees, as with `verify --catalog`.
The site's catalog is derived from the index, so an experiment with no published
runs never appears. The web build itself contains no experiment catalog.

`--dry-run` prints each store's filtered run count and the files and sizes it
would write, without creating or deleting anything. Every invocation rebuilds
the whole index from complete run-object pairs. It deletes the output directory
and rewrites it in full, writing `index.json` last. Shards for experiments no
longer present are removed, together with their catalog entries and assets.
Unreadable, malformed or disappeared cards produce warnings and are skipped,
so healthy runs can still be indexed. Failure to list a store aborts the rebuild
before replacing the destination.

The root `index.json` lists experiment names, their run counts, the total count,
and `built_at`. Each `experiments/<experiment>/index.jsonl` contains one row
`{"store":"<public HTTPS base>","card":<run.json as a JSON value>}` per run.
The card is compactly re-serialized with Unicode retained and key order
preserved. This derived cache is never authoritative. Opening a run fetches the
original card and compressed stream from the row's store; raw display uses those
original bytes and decompressed lines.

The index includes `catalog.json` with current manifests, shared render hints and
versioned experiment hints for indexed experiments only. README assets are copied
to `catalog/assets/<name>/`, dereferencing the manifest directory's symlinks.
A missing manifest warns and leaves the experiment visible through its runs,
without a manifest or experiment assets. Unknown schema versions
fall back to shared hints. Published mode hides launch, publish and jobs controls;
it requires no Node process. Without `site.json`, the app uses its local server.

The browser reads `index/catalog.json`, `index/catalog/assets/<name>/`, `index/index.json` and
`index/experiments/<experiment>/index.jsonl` relative to the app's own directory.
An app served at `/adb/` reads `/adb/index/index.json`; the same layout works
at the site root. It revalidates the index once a minute and caches `runs/`
objects for the session. Run objects are fetched from each row's `store`,
following redirects, including signed CDN URLs, with credentials omitted on
every hop. Damaged rows and missing shards appear as diagnostic entries without
hiding healthy runs.
Unreadable run objects and identity mismatches also become diagnostic entries
with reasons. A pure-JavaScript zstd decoder preserves the stream's decompressed
lines for raw display. Large params are thinned for listings and expand from the
original card; the index's re-serialized card never supplies raw card bytes.
The local Node server does not read buckets in published mode.
