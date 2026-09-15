# Release process

The CI library uses immutable semantic-version tags in the v2.x series. A
release starts from a reviewed commit on main after the pull request has
passed workflow validation, dependency review, CodeQL, the DCO check, and the
repository test suite.

## Candidate checklist

1. Create `docs/releases/v2.x.y.md` for the exact tag. It must contain a
   `## Changelog` section with functional and security changes, affected
   callers, migration notes, and the checks and support window for the
   release. The release workflow rejects a tag without this file.
2. Run nix flake check, scripts/check.sh, and the complete Python test suite.
3. Run a representative caller repository against the candidate commit.
4. Run scripts/sync-release.py in check mode for all known callers before
   publishing a new shared release.
5. Tag the exact reviewed commit. Do not tag a dirty working tree.
6. Verify the release using the commands below before announcing it.

## Verification

For example:

```console
gh release download v2.x.y --repo nix-forge/ci --dir release-v2.x.y
(cd release-v2.x.y && sha256sum -c nix-forge-ci-v2.x.y.tar.gz.sha256)
gh attestation verify release-v2.x.y/nix-forge-ci-v2.x.y.tar.gz \
  --repo nix-forge/ci \
  --signer-workflow nix-forge/ci/.github/workflows/slsa-source-release.yml \
  --signer-digest bf01ac186602f722c516823520aef97c8670fcb8
```

The expected release identity is the `nix-forge/ci` repository and the pinned
`nix-forge/ci/.github/workflows/slsa-source-release.yml` reusable builder. Keep
the digest in this command synchronized with `.github/workflows/release.yml`.

The reusable builder creates a source archive, a SHA-256 file, a release
manifest, and an OIDC-backed SLSA build attestation before the protected
publisher job receives the files. The publisher verifies the exact tag, bytes,
and signer workflow before creating a draft GitHub Release, then publishes it
only after all assets are attached. This is required for immutable-release
repositories. It does not publish opaque compiled assets. Release notes name
the actor and workflow, list public inputs and outputs, describe the security
assessment, and state the support and end-of-life window. A release stops
receiving security updates when its support window ends or when the next major
contract removes it from the supported matrix.

## Compatibility

Reusable workflow inputs and outputs are public APIs. A breaking contract
change requires a migration note, updated contract tests, and caller updates
before the release tag is created.
