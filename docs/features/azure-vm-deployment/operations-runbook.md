# Running the FIKRAH demo

## Access

- API: https://fikrah-api-23107c84.austriaeast.cloudapp.azure.com
- Swagger: https://fikrah-api-23107c84.austriaeast.cloudapp.azure.com/docs
- Readiness: https://fikrah-api-23107c84.austriaeast.cloudapp.azure.com/ready
- Azure: `rg-fikrah-prod-austriaeast`, VM `vm-fikrah-prod`.

Use OTP for email verification and password recovery. Login remains password or Google ID-token exchange. Flutter needs no browser cookies or local development UI.

## Start and inspect

On the VM:

```bash
cd /opt/fikrah/current
sudo docker compose --project-name fikrah \
  --env-file /etc/fikrah/compose.env --env-file release.env \
  --file compose.yaml ps
```

Use the same Compose prefix with `up -d` to start services, or `logs --tail 100 SERVICE` to inspect a selected service. Do not print resolved environment configuration or private credentials.

Services restart automatically after Docker starts. Persistent database volumes remain when containers restart. Never run `down -v` or prune live volumes.

## Updates

Normal releases use [main-only CI/CD](../continuous-delivery/main-only-deployment.md). Merge tested changes through `dev` into `main`; the pipeline builds/tests/packages the image and installs it on this VM. The manual commands below are a fallback.

The deployment scripts live under `infra/azure-vm/`. Generate configuration once with `configure_runtime.py --otp-only`, transfer securely, and install with `install_runtime.sh`. Existing credentials are deliberately retained.

For an application update, commit reviewed changes, package using `package_release.sh PRIVATE_OUTPUT_PARENT`, transfer the package through verified SSH, and run `sudo install_release.sh STAGED_RELEASE_DIRECTORY` on the VM. The installer checks image/configuration, stops demo writers during migration, starts services and checks readiness. No automatic backup runs. Do not automatically downgrade the database if a migration fails.

The current release is identified by `/opt/fikrah/current/release.json`, not by a hard-coded revision in this guide. CI/CD tags each image `fikrah:<main-commit>` and deploys matching host scripts. Use the release manifest and GitHub deployment run when identifying the running version.

## Demo limits

There is one VM and one data disk. Backups and scheduled backup downtime were removed at the user's request. Losing the disk can lose data. No autoscaling, additional managed database, cross-region recovery or paid monitoring service is configured.

Check `/ready` if a demo request fails. Provider latency and SMTP latency remain external to VM placement. The fixed initial VM size is adequate for the observed small acceptance flow, not a load-tested concurrency guarantee.
