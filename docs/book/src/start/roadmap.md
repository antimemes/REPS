# Roadmap

## What works today?

- [x] Local CLI execution through Nix.
- [x] Saved credential profiles.
- [x] Experiment contributions through ordinary in-repository pull requests.

## What comes next?

- [ ] Record source, environment and external inputs, and test with disposable runs so we can derive additional fingerprints later.
- [ ] Define a versioned data format and replication instructions.
- [ ] After those checks, run experiments and publish our data with a read-only website.

Possible later additions:

- [ ] Parameter presets and reuse of run configuration.
- [ ] Analysis and annotations about which runs can meaningfully be compared, possibly in another tool.
- [ ] External data deposits with attribution and review.
- [ ] An agent authoring skill and sandbox tests of its instructions.
- [ ] Dynamic credentials and declarations for credentials other than model access.
- [ ] Stronger execution isolation and a proxy that records requests.
- [ ] Pausing an execution and starting branches from it.
