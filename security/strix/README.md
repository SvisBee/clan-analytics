# Strix inactive scaffold

Status: researched, documented, scaffolded and inactive. Strix is not installed and has not been run.

This directory is not a launcher. It contains no executable command, credential, real target, production configuration, Pages target, scan result or CI integration. Any commands mentioned in linked upstream material are documentation, not permission to execute them.

A future run requires all of the following before any installation or execution:

1. Review [the readiness assessment](../../docs/security/strix_readiness.md).
2. Copy `scope.example.md` to an ignored local location and replace every placeholder with an approved value.
3. Record each separate approval from the readiness matrix.
4. Keep credentials in approved local secret storage, never in Git or scope documents.
5. Keep results in an explicitly approved ignored location outside Git.
6. Confirm the target is owned, non-production and exactly bounded. GitHub Pages and the public project site are not allowed targets.

There is no automatic setup, Docker action, network request, scan, retry or cleanup. CI integration is not planned at this stage.
