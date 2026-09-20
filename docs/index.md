# nix-forge CI

This repository publishes shared GitHub Actions and reusable workflows for the
nix-forge organization. Product repositories keep their build definitions and
tests; this library owns the common workflow contracts and the tools that check
them.

Start with the [architecture and migration guide](architecture.md). It explains
which behavior belongs in a caller, a composite action, or a reusable workflow.
The [repository README](https://github.com/nix-forge/ci#readme) lists every
supported workflow and action with its required permissions and inputs.

Consumers must pin a tested release commit, not a moving branch or tag.
Dependabot can then propose reviewed updates. A reusable workflow cannot grant
itself permissions that its caller did not provide.

## Maintain the documentation

Build the same artifact that Pages publishes:

```console
nix build .#documentation-site
```

Preview edits locally:

```console
nix develop .#docs --command mkdocs serve --config-file site/mkdocs.yml
```

Action implementation details remain beside each action. This site documents
the stable shared interface and design decisions instead of copying action files.
