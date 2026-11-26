# Azure VM operator runbook

## Before executing

Use the reviewed deployment branch and its immutable commit. Commands below are operator procedures, **not evidence they have run**. Current target:

- Subscription `Azure for Students` / `0de07754-9825-4fc4-b0b8-eb147c12d6f5`.
- Resource group `rg-fikrah-prod-austriaeast`, location `austriaeast`.
- VM `vm-fikrah-prod`; SSH user `fikrahadmin`.
- Backup account `stfikrahbackup2310`, private container `backups`.

Required inputs: approved SSH public key and current caller public IPv4 `/32`, available DNS label/public hostname, ACME contact email, valid SMTP/provider secrets, approved Google client ID and public HTTPS email-action URL. Keep private keys and runtime configuration outside Git. Public email-action routing is a product integration decision; do not substitute the local web UI.

## 1. Render and validate infrastructure

From the repository root, using the existing project virtualenv:

```bash
python infra/azure-vm/prepare_parameters.py \
  --ssh-public-key /absolute/path/to/key.pub \
  --admin-cidr YOUR_PUBLIC_IPV4/32 \
  --dns-label APPROVED_DNS_LABEL \
  --output .copilot-azure/operator.parameters.json
az bicep build --file infra/main.bicep --stdout >/dev/null
```

After separate deployment approval, explicitly select the subscription on every Azure operation:

```bash
az deployment sub what-if --subscription 0de07754-9825-4fc4-b0b8-eb147c12d6f5 \
  --location austriaeast --template-file infra/main.bicep \
  --parameters @.copilot-azure/operator.parameters.json
```

Inspect scope, names, role assignment, firewall, cost and unexpected deletes. Stop if the plan differs. Only then run the equivalent `az deployment sub create` and retain its nonsecret outputs in ignored local state. Verify the SSH host key independently through Azure Run Command/serial console before transferring secrets; do not disable host-key checking or blindly trust `ssh-keyscan`.

## 2. Bootstrap and install runtime configuration

Transfer `bootstrap.sh` over verified SSH and run with sudo on the **new dedicated VM only**. It installs current signed Docker packages/security updates, changes Docker and SSH configuration and restarts Docker. Do not run it against an existing shared server. Verify `sshd -t`, key-only access in a second SSH session, `docker compose version`, Docker service and the IMDS firewall rule. Retain Azure Run Command access for recovery if the caller IP changes.

Generate credentials locally using the existing `.venv`, with the real approved public hostname and callback URL:

```bash
python infra/azure-vm/configure_runtime.py \
  --source-env /absolute/path/to/source.env \
  --output-dir .copilot-azure/runtime \
  --hostname APPROVED_PUBLIC_HOSTNAME \
  --email CERTIFICATE_CONTACT_EMAIL \
  --email-action-url https://APPROVED_HOST/APPROVED_ACTION_PATH
```

The generator reads but never edits the source. It creates independent DB/Redis/Qdrant/JWT credentials. No configuration is emitted to stdout. Transfer the protected directory, run `sudo install_runtime.sh STAGED_CONFIG_DIRECTORY`, then remove only the transferred staging directory after verifying the protected installation. Keep a separately secured recovery copy. Never print `docker compose config`, `docker inspect`, `/etc/fikrah/app.env` or backup contents into shared logs: these contain credentials. Use `config --quiet`.

## 3. Package and install a release

The checkout must be clean and committed. The package uses `git archive HEAD`, not local untracked files. It resolves the Python base to a digest and uses hash-locked requirements. Keep the original release bundle off the VM for disaster recovery.

```bash
infra/azure-vm/package_release.sh .copilot-azure/releases
```

Transfer the generated SHA-named release directory over verified SSH. On the host:

```bash
sudo /path/to/staged-release/install_release.sh /path/to/staged-release
```

The script refuses a release directory that already exists: investigate partial state before retrying. Do not delete databases or overwrite existing credentials to bypass that refusal. It verifies package file hashes and loaded image ID/platform/revision before changing services. It uses the dedicated Compose project `fikrah`; never point it at a development host's project of the same name.

For upgrades, a verified off-VM backup is mandatory before migration. Failed upgrades may leave writers stopped intentionally. Preserve logs and backups. Confirm the Alembic revision and actual schema before choosing recovery; do not automatically run a downgrade or old release. `/opt/fikrah/current` advances only after local readiness. `/opt/fikrah/previous` identifies the prior successful release. The host copy of `image.tar.gz` is removed after loading to bound space; its original checksum remains provenance, and the loaded image plus the retained off-host bundle are the recovery artifacts. Do not indiscriminately prune images or volumes.

## 4. Acceptance before handover

Record results and timestamps, distinguishing real and mocked checks:

1. `docker compose` with `/etc/fikrah/compose.env`, current `release.env` and current `compose.yaml`: `ps`; inspect restart counts, OOM status and protected logs.
2. Verify PostgreSQL TCP readiness and authenticate as `fikrah_app`. Verify that it is not a superuser and cannot create roles/databases.
3. External HTTPS certificate/hostname, `/health`, `/ready`, documented API status codes and SSE deltas/final event. Check database ports are unreachable externally.
4. Registration/OTP/verification/login/reset/refresh/logout/Google on approved audiences; no local browser proxy dependency. Send emails only to authorized test recipients.
5. Create/process/retry a note and observe each dedicated Celery queue's actual execution; search, connections and chat with sources. Check provider timeout/error handling and SMTP delivery.
6. Measure request latency and queue wait separately, plus provider timings. Test representative load and note sizes. Record CPU credit balance, RAM/OOM, disk free/inodes and Redis usage/evictions. The initial memory caps/concurrency are unproven starting values.
7. Run backup upload and the isolated restore drill below; record Blob name, digest, size and recovery duration without secrets.
8. Reboot the VM in an approved window. Verify services, credentials/volumes, HTTPS, IMDS firewall and queues recover.

## 5. Backup and upload retry

```bash
sudo python3 /opt/fikrah/current/backup.py
sudo python3 /opt/fikrah/current/backup.py \
  --upload-existing /var/lib/fikrah/backups/EXACT_TIMESTAMP.tar.gz
```

Retry only the specific complete archive after fixing the upload cause. A successful blob commit followed by a lost response can yield HTTP 412 on retry; inspect the existing blob size and `sha256` metadata before recording a receipt manually. Never assume a failed response proves no backup exists. Failed/unresolved backups deliberately block new captures. Never delete the last recoverable backup simply to clear the check.

Azure role assignment propagation can initially delay access. Keep the subnet firewall enabled while diagnosing. Do not enable shared-key access or public blobs. Credentials are included in the backup; access is sensitive even with storage encryption at rest. The VM identity's container role allows deletion; seven-day soft delete is not immutable retention.

After a successful **restore drill** and approved maintenance window:

```bash
sudo systemctl enable --now fikrah-backup.timer
sudo systemctl list-timers fikrah-backup.timer
sudo journalctl -u fikrah-backup.service
```

The timer drains/stops writers and can interrupt clients. Define operational ownership and failure notification before enabling unattended operation. Monitor backup failures/disk space; the scaffold has bounded logs but does not configure an external alert recipient.

## 6. Isolated restore drill and disaster recovery

Run this on an isolated replacement host/empty Compose project, never on live volumes. A temporary Azure host incurs additional cost and needs approval. A same-host isolated drill avoids that charge but requires disk headroom and network separation. Prevent restored workers, SMTP and provider calls from running during validation.

1. Retain the original release bundle, retrieve the selected blob through an authorized identity/network, and verify archive SHA-256/size against its receipt or Blob metadata.
2. Inspect archive members before extraction: reject absolute paths, `..`, links and device files. Extract into a protected empty directory. Verify every file against `checksums.json`; do not source unverified shell/env files.
3. Bootstrap the replacement with the same pinned datastore versions. Restore protected runtime credentials with original ownership. Preserve the DB app/admin separation. Create **fresh** volumes; never overlay old and restored storage.
4. Start PostgreSQL only, allow its app-role initialization to complete, then restore `postgres.dump` with `pg_restore --exit-on-error --single-transaction --no-owner --role=fikrah_app -U fikrah_admin -d fikrah`, over container stdin. The database must be empty. Review `postgres-roles.sql` for account recovery, but do not blindly replay superuser/duplicate-role statements against initialized roles.
5. With Qdrant stopped and an empty storage volume, restore the **full storage** snapshot using the pinned Qdrant binary's `--storage-snapshot` startup option. Mount the snapshot read-only. Do not use collection-only recovery for this full snapshot. Validate the restored collection and payloads before normal startup.
6. With Redis stopped and an empty Redis data volume, restore `redis.rdb` with uid 999. Bootstrap Redis with AOF temporarily disabled so the RDB is read; verify contents, then enable AOF and confirm persistence before restoring the standard config. An empty/new AOF can otherwise take precedence over the RDB. Queued tasks may be redelivered; validate existing generation/idempotency guards.
7. Load the retained exact app image. Verify loaded image ID/revision and restored Alembic revision. Start the API without workers first, with outbound email/provider calls blocked for the drill. Check ownership/isolation, counts, notes, conversations and PostgreSQL/Qdrant agreement. Do not run upgrades automatically during a restore drill.
8. Review Redis scheduled/queued tasks before enabling workers. Validate recovery using authorized dummy data, then record the evidence and destroy only the disposable drill resources/volumes.
9. For actual recovery, stop writes to the old instance before cutover, approve DNS changes, review credentials/session validity, and perform external acceptance. Do not run two writers against independent restored copies.

Reference: [Qdrant snapshots](https://qdrant.tech/documentation/operations/snapshots/). No restore drill has passed merely because this runbook exists.

## Maintenance and limitations

- Schedule host/Docker security updates and reboots; a single host has downtime.
- Monitor CPU credits; increase size only after measurement and cost approval.
- Expand storage before backups lose conservative headroom; separate data disks/managed databases are future options.
- Provider latency remains outside VM placement control. No claims of measured response improvement until actual measurements exist.
- No automatic cross-region recovery, external alerting or HA is configured.
- Cleanup/deallocation must explicitly account for retained disk, static IP and backup storage; never delete production resources as part of an ordinary deploy.
