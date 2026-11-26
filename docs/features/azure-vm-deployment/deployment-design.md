# Azure VM deployment design

## Scope and approval

Deploy the existing backend as containers on **one Ubuntu 24.04 x64 VM** in Austria East. Flutter remains the production client. The development web UI, demo accounts, local databases and Mailpit are excluded. Application source, public HTTP contracts, local `.env`, existing migrations and local unmerged categories work are unchanged.

Infrastructure and runtime scaffolding are prepared on `feature/azure-vm-compose` from `origin/dev` at `dc66bd1`. Paid provisioning requires a separate deployment approval. A successful Bicep build or offline test is not proof of an Azure deployment, a restore or production capacity.

## Target and estimated cost

| Item | Selection | Estimated monthly USD |
| --- | --- | ---: |
| VM | Standard_B2als_v2, 2 vCPU / 4 GiB, no zone pin | 31.536 |
| OS/data disk | 64 GiB Standard SSD LRS, E6 | 4.800 |
| Public IPv4 | Standard regional static IP | 3.650 |
| **Fixed baseline** | 730 hours | **39.986** |
| Backup storage | Hot Blob, Standard LRS | Usage based, additional |

Estimate checked against Azure Retail Prices on 2026-11-26. Excludes disk transactions, backup bytes/operations/soft-deleted retention, egress, provider/email charges, taxes and future price changes. Stopping the guest OS does not stop compute billing; deallocation stops VM compute charges, while disk/IP/storage remain chargeable. This is a small initial deployment, with no availability SLA promised by this design.

Austria East is the nearest geographically among the subscription's allowed locations. This is **not a measured network latency ranking**. Italy North's VM rate was marginally lower ($0.0431 vs $0.0432/hour). The other allowed regions are Poland Central, Denmark East and Switzerland North. Actual user-to-API and API-to-LLM latency require measurement after provisioning. The B-series CPU credit model can throttle sustained work; the 4 GiB RAM budget requires load validation.

Sources: [Azure retail pricing](https://learn.microsoft.com/en-us/rest/api/cost-management/retail-prices/azure-retail-prices), [Basv2 sizing](https://learn.microsoft.com/en-us/azure/virtual-machines/sizes/general-purpose/basv2-series).

## Topology and responsibilities

```text
Flutter -> HTTPS :443 -> Caddy -> API :8000
                                |-> PostgreSQL
                                |-> Redis -> dedicated Celery queues
                                |-> Qdrant
worker -> LLM/embeddings providers; email-worker -> SMTP
host backup job -> managed identity -> private Blob container
```

- `infra/main.bicep`: subscription deployment and explicit resource names.
- `infra/modules/`: network, VM and backup storage; no application secrets in ARM parameters, extensions or user data.
- `infra/azure-vm/compose.yaml`: standalone production definition. Never overlay it on the development Compose file.
- `prepare_parameters.py`: validates the active subscription, public SSH key and canonical public IPv4 `/32`; renders private parameters without provisioning.
- `configure_runtime.py`: creates independent server credentials from explicitly selected provider/SMTP settings; never modifies its source file.
- `bootstrap.sh`: dedicated fresh Ubuntu host setup, official signed Docker repository, SSH hardening, log bounds, host firewall rule for container metadata access.
- `install_runtime.sh`: root-only credential installation; refuses overwrite.
- `package_release.sh`: clean committed Git archive -> digest-resolved Python base -> labelled amd64 image -> checksummed release bundle. Existing requirements are hash locked. Runtime credentials are excluded.
- `install_release.sh`: checksums/image identity/config checks -> pre-migration off-VM backup on upgrades -> migration -> services -> `/ready` -> current release pointer.
- `backup.py`: maintenance lock, bounded capture, PostgreSQL/Redis/Qdrant snapshots, private managed-identity block upload and retention receipts.

## Security boundaries

Only ports 80/443 are public. SSH is restricted to one approved public IPv4 `/32` and public-key login. NSG explicitly denies other inbound traffic, including default VNet allowances. PostgreSQL, Redis and Qdrant have no published host ports and share an internal Docker network. API/LLM and email workers receive outbound networking only where needed. API trusts forwarded headers only from Caddy's fixed address; no wildcard proxy trust.

Caddy obtains HTTPS certificates for the approved public hostname, preserves SSE flushing and does not log URL query tokens. Uvicorn access logs are disabled for the same reason. Application containers retain the application's non-root user, read-only filesystem and dropped Linux capabilities. Datastore initialization retains necessary startup privileges. Container resource limits and local Docker log rotation bound some failure modes; they do not prove adequate capacity.

PostgreSQL's runtime role is separate from the bootstrap administrator. Redis and Qdrant require independent random credentials. The VM generates no provider keys; the operator supplies existing valid provider/SMTP credentials securely. Generated files are mode 0600 in a 0700 directory; they may only be written outside Git or under ignored `.copilot-azure/`. Compose 2.30+ is required for `env_file.format: raw` so special characters are preserved.

The backup storage account rejects shared-key auth and anonymous access, enforces HTTPS/TLS 1.2, and admits only the VM subnet via a Storage service endpoint. Its public endpoint exists for the service endpoint; it is **not generally network accessible**. VM managed identity has Blob Data Contributor only at the backup container scope. That role can read/delete backups; it is not write-only or immutable storage. Host root remains trusted. A DOCKER-USER rule blocks container access to IMDS; verify it after Docker startup and reboot. It is not a complete defence against host compromise, and Docker restart can create a transient rule-application window.

## Data durability and operations

All database volumes live on the persistent OS disk. The OS disk is detached rather than deleted when removing the VM; this does not replace backups. There is one host/disk failure domain. Backup blobs are in a separate storage account in the same region with LRS; regional disaster recovery is not provided.

Backups pause API/proxy and drain Celery writers before capture. Ordinary backups restart only writers that were running. Upgrade backups intentionally keep writers stopped until the migration and new release succeed. This causes a maintenance interval. No automatic schema downgrade or restart of old code against an unknown schema is attempted.

Admission checks reject a new backup if an earlier upload is unresolved, or estimated free space is below three times source volume sizes plus 512 MiB. This conservative check cannot prevent concurrent disk growth. Uploads use 8 MiB blocks and a final conditional commit. An upload receipt is written only after success. Keep the two newest successfully uploaded local archives; blobs expire after 30 days with seven-day soft delete. Failed full archives remain for explicit upload retry; partial compression artifacts are removed. Monitor space and investigate the first failed backup immediately.

The nightly timer is installed **disabled**. Proposed window: 03:30 Africa/Cairo, randomized by up to five minutes. Enable only after the user accepts the maintenance interval and an off-VM restore drill succeeds. The runbook explains recovery and the evidence required.

## Validation limits and release gates

Before provisioning: Bicep compilation, schema/name/quota/image checks, offline runtime tests, secret-output review and explicit subscription/cost approval.

Before production handover: Azure what-if/validation, pinned image pulls, first boot, application-role authentication, external HTTPS, `/ready`, real queue consumption, auth/OTP/Google, note processing, search/chat/SSE, backup upload, isolated restore drill, reboot recovery and representative latency/RAM/disk/CPU measurements. Never treat container `running` or `/health` alone as readiness.

No production email-action URL, Flutter app links/platform identifiers or Google audience changes are invented by this deployment. Supply/approve these integration values before final configuration. Capacity scaling, high availability and automatic alert delivery remain outside this initial low-cost topology.

See [operator runbook](operations-runbook.md) and [deployment phase](../../phases/deployment-readiness/azure-vm-readiness.md).

Recorded checks and limits: [scaffold validation results](validation-results.md).
