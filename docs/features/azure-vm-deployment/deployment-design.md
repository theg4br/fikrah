# Azure demo deployment

FIKRAH runs on one Ubuntu VM in Austria East using Docker Compose. This is a college demo, not a high availability deployment.

- VM: `Standard_B2als_v2`, 2 vCPU / 4 GiB RAM.
- Disk: 64 GiB Standard SSD; PostgreSQL, Redis and Qdrant use persistent Docker volumes.
- Services: API, four Celery workers, scheduler, PostgreSQL, Redis, Qdrant and Caddy.
- Caddy provides public HTTPS and unbuffered SSE. Only ports 80/443 are public; SSH is restricted to the operator's IP.
- Flutter connects directly using JSON/Bearer tokens. No production website or local web proxy is deployed.
- OTP verification/recovery, password login and configured Google token exchange retain the existing contracts. Legacy email links are intentionally unconfigured.
- The user explicitly requested removal of the backup system and its Azure storage. No backup jobs, snapshots, Blob archive storage or automatic pre-upgrade backup are part of this demo. Data persists across container restarts; losing/deleting the VM disk can lose the demo data.

The fixed estimate is approximately USD39.99/month at 730 hours, plus disk transactions, egress, LLM/email usage and taxes. No backup storage charges are intended after its removal. Deallocating the VM stops compute billing; disk and public IP remain chargeable.

The existing VM managed identity and container metadata firewall are harmless host configuration, not a running backup service. They do not add a paid resource. Application configuration stays private under `/etc/fikrah`; credentials are never committed.

See [operations](operations-runbook.md) and [validation](validation-results.md).
