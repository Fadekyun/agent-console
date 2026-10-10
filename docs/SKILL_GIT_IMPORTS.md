# Staged Git skill imports

Staging 0.19 adds Git HTTPS sources to the same inspect/review/activate lifecycle as local packages. Import is an explicit operator action. It never activates the package or executes its scripts.

```sh
agentctl skills import https://github.com/OWNER/REPOSITORY.git --revision main --subdirectory skills/package-name
agentctl skills inspect-import import-ID
agentctl skills activate import-ID --hash INSPECTED_PACKAGE_HASH
```

The Skills page offers the same source, revision and package-directory fields. Local web paths remain contained in the configured workspace. The default Git revision is HEAD; an exact full commit SHA is preferable when reviewing a known source. Root-level packages can omit `--subdirectory`. Local imports reject Git-only options rather than ignoring them.

## Fetch and extraction

The importer fetches anonymously over HTTPS into a fresh bare repository with an empty template, disabled credentials/hooks/redirects, no inherited Git configuration or task environment, and HTTPS as the only allowed transport. It resolves the requested ref to an exact commit and reads tree/blob objects directly. There is no checkout, filter execution, submodule recursion, package installer or dependency installation. A package requires a portable valid `name` in `SKILL.md`.

The watchdog bounds fetching/extraction to 60 seconds and 128 MiB of temporary repository/package files, with bounded stdout/stderr. Packages retain the existing limits: 512 files, 2 MiB per file and 16 MiB total. Oversized trees, non-regular entries, symlinks, submodules and escaping paths are rejected before activation. Temporary fetch data is removed on success/failure. Native errors are replaced with generic errors so remote-supplied text cannot expose credential-like output.

Credential-bearing URLs, query strings and fragments are rejected. Private repositories can be fetched separately through an operator's existing authenticated workflow, then imported locally. Such an import records a local source; Console does not assert that it fetched or verified a remote commit. Secret detection remains heuristic and human inspection still matters.

## Provenance and drift

The import receipt records the requested URL/ref/directory, resolved full SHA and original package hash. Inspection separates fetched provenance from source/revision claims declared inside the package. Activation binds the inspected package hash, preserves an existing canonical folder and retains the import identity across later review decisions.

Selected-session delivery retains the fetched provenance and both original/current content hashes. A local edit invalidates the old review. If that edit is explicitly reviewed again, it may become eligible, but its receipt still says that the bytes differ from the fetched commit. No Git repository metadata is copied into the canonical package or session snapshot.

The policy JSON additions are additive; no database schema migration is required. Back up the library, policy/imports and delivery history together. Rollback to 0.18 preserves the package and trust/hash checks but loses Git import and origin display; it may show the package-declared revision. Preserve newer receipts and use 0.19 when reconciling fetched provenance.

## Verification — 2026-10-02

Real Git-object tests cover selected-tree extraction, exact commit attribution, inert executable files, activation/re-review/session receipts, locally edited provenance, symlink/submodule/oversize/secret rejection, bad protocols/credentials/traversal, redacted native errors, timeout and staged drift. Authenticated API and desktop/360px/390px browser tests verify forwarding the selected ref/path, inspection and separate hash-bound activation.

A real anonymous HTTPS fetch of `openai/skills` selected `skills/.system/skill-creator` at commit `49f948faa9258a0c61caceaf225e179651397431`, package hash `55e5273d915595fdb391eb737b902c81abe41a164b4bbb4fa258a5a5e033d251` (seven files). It stayed imported-unreviewed in temporary test state and was never activated. This verifies transport/extraction, not a trust endorsement of that third-party package.
