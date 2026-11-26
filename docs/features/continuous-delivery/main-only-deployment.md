# Main-only continuous delivery

## Release path

`feature/*` → pull request to `dev` → passing `backend` check → pull request to `main` → passing CI → deploy the tested image to the existing Azure VM.

A direct authorized push to `main` triggers the same pipeline. Pull requests, feature/dev pushes, tags and manual workflow runs never deploy. The deploy job explicitly requires both `event_name == push` and `ref == refs/heads/main`; the GitHub `demo` environment additionally permits only `main`.

## CI

`.github/workflows/backend-checks.yml` runs Python 3.12, installs hashed dependencies, lints application and infrastructure code, builds the runtime image, runs the complete suite with disposable PostgreSQL/Redis and Docker, rejects skipped required integration suites, applies migrations and checks API startup/metadata/Celery registrations. Artifact actions use Node.js 24 and pinned commit revisions. Provider credentials are not available to CI. Deployment tooling tests are part of the same suite.

Main builds label the image with the exact commit. Only after tests pass is that exact image packaged, checksummed and transferred to the deploy job. It is not rebuilt on the VM. The one-day Actions artifact and a `demo-<commit>` prerelease distribute the credential-free runtime bundle; they are deployment artifacts, not database backups. Published bundles contain neither `.env` nor production data, local UI or work instructions. A rerun refuses to replace an existing release with different bytes.

## Azure access

- GitHub environment: `demo`, branch allowlist `main`.
- OIDC issuer: `https://token.actions.githubusercontent.com`.
- Subject: `repo:theg4br@184538050/fikrah@1450332931:environment:demo`.
- Audience: `api://AzureADTokenExchange`.
- The numeric owner/repository IDs are part of this repository's immutable GitHub subject. Verify the actual issuer/subject/audience from the Azure Login claim diagnostics when provisioning; the legacy name-only subject does not match this repository.
- Entra application: `fikrah-github-deploy`, without a client secret.
- Custom role: `FIKRAH GitHub VM deploy`, assigned only to `vm-fikrah-prod`, with VM read and Run Command actions. Run Command executes as root; this is privileged deployment access, not a sandbox.
- Environment secrets: `AZURE_CLIENT_ID`, `AZURE_TENANT_ID`, `AZURE_SUBSCRIPTION_ID` (identifiers, no provider/app credentials).
- Environment variables: `AZURE_RESOURCE_GROUP`, `AZURE_VM_NAME`.

No SSH firewall expansion, self-hosted runner, registry, backup storage or additional paid host is required. Runtime app/provider/SMTP credentials remain in `/etc/fikrah` on the VM.

## Install and failure handling

Azure Run Command downloads the immutable public release asset, verifies the checksum supplied by the trusted workflow and rejects unsafe archive members. The existing installer verifies image identity, pauses writers, applies migrations, starts Compose and advances the current release after readiness. Protected installer output stays under `/var/log/fikrah-deploy/<commit>.log`.

The runner requires an explicit guest success marker for the expected commit; Azure's command exit status alone cannot report success. It then checks public HTTPS `/ready`. A rerun of the currently healthy revision is a no-op. An incomplete release requires inspection, rather than silently overwriting its directory or retrying a migration.

Main deployments are serialized and are not cancelled by a newer commit. There is brief downtime during migration/replacement on this one VM. No automatic database downgrade, destructive volume cleanup or backup occurs. If deployment fails after writers stop, inspect the protected log and migration state before recovery; the previous image is not automatically safe against the new schema.

## Repository controls

Require the `backend` check for `dev` and `main`, prohibit force pushes and branch deletion, and retain merge commits. Feature changes target `dev`; only release changes reach `main`. The initial creation of `main` from passing `dev` was explicitly authorized by the user.

The [operator runbook](../azure-vm-deployment/operations-runbook.md) covers service inspection and manual deployment. Real Google/mobile, audio, sustained load and host reboot acceptance remain outside the automated CI claims.

## References

- [GitHub push events](https://docs.github.com/en/actions/reference/workflows-and-actions/events-that-trigger-workflows#push)
- [Azure OIDC authentication](https://learn.microsoft.com/en-us/azure/developer/github/connect-from-azure-openid-connect)
- [Azure VM Run Command](https://learn.microsoft.com/en-us/azure/virtual-machines/linux/run-command)

The first main run exposed the legacy-subject mismatch before application installation; Azure trust was corrected to the observed immutable subject and the failed deploy job was retried using the existing tested artifact. See [GitHub immutable subjects](https://docs.github.com/en/actions/reference/security/oidc).
