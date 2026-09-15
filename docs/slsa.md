# SLSA build provenance

The CI library provides the trusted, organization-owned builders used by
release-producing repositories. Callers must pin these reusable workflows to
the exact reviewed commit, and the builder must create the distributable bytes
and their attestations in the same isolated GitHub-hosted job.

## Reusable builders

- `slsa-source-release.yml` creates a tagged `git archive`, a SHA-256 file, and
  a release manifest. It validates the immutable tag and tag-specific release
  notes, creates SLSA build provenance for every release subject, and uploads
  the exact staged files for a separate publisher job.
- `slsa-nix-seal-release.yml` builds the supported `nix-seal` package on each
  supported platform, generates a CycloneDX SBOM, records the locked-input
  digest and build metadata, and creates both build-provenance and SBOM
  attestations before upload.

The builder jobs have no release-write permission, no long-lived signing key,
and no shared Nix release cache. Publication belongs in a separate job behind
the protected `release` environment. That job may publish only the files
downloaded from the completed builder and must verify their attestations with
the expected signer workflow before creating a release.

Consumers should verify an artifact with the GitHub CLI, for example:

```console
gh attestation verify artifact.tar.gz \
  --repo nix-forge/REPOSITORY \
  --signer-workflow nix-forge/ci/.github/workflows/slsa-source-release.yml \\
  --signer-digest da90bfbbb18cfa1ceb176d55d2a1c3cd3e6b1049
```

The digest is the reviewed builder commit used by the current release
workflows. Callers must update it, and their verification documentation, in
the same change when they roll the builder forward.

This is a SLSA Build track control for named release artifacts. It does not
make routine test outputs, source files, or a Nix flake itself a Build Level 3
artifact. See the [SLSA Build specification](https://slsa.dev/spec/v1.2/) and
[GitHub's Level 3 guidance](https://docs.github.com/en/actions/how-tos/secure-your-work/use-artifact-attestations/increase-security-rating)
for the producer and consumer requirements.
