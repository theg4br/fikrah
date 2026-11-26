# Scaffold validation results

Checked on 2026-11-26 before paid provisioning.

| Check | Result | Evidence/limit |
| --- | --- | --- |
| Bicep build | Passed | Installed compiler 0.48.1; no schema/linter errors; CLI advertises newer version |
| Scaffold conformance | Passed | Script result `passed: true`, empty failures |
| IaC security/module review | Passed applicable checks | Explicit names/SKU, SSH key and /32, managed identity, private backup admission; no deployment run |
| Runtime safety tests | 16 passed | Dummy secrets, raw env handling, network boundaries, /32 rejection, failed backup admission, block upload protocol and packaging allowlist |
| Local ASGI startup/task contracts | Passed | `/health`, OpenAPI, SQLAlchemy metadata and exact Celery registration set, without provider calls |
| Existing backend suite | 593 passed, 46 skipped | Existing project virtualenv; PostgreSQL/Redis/Docker integration envs not supplied for this suite; one Google SDK deprecation warning |
| CI lint scope plus infra | Passed | `ruff check app tests scripts infra` |
| Whole-tree extra lint | Existing failures | `ruff check .` reports import-order I001 in four unchanged historical migrations (20261109_01, _02, _03, _05); migrations intentionally untouched |
| Shell syntax | Passed | `bash -n` for bash scripts; `sh -n` for PostgreSQL init |
| Pinned PostgreSQL image | Pulled; fresh initialization passed | Isolated disposable container; TCP-ready app role authenticated and created a rolled-back table; role had no superuser/create DB/create role flags |
| Pinned Redis and Caddy images | Pulled | Qdrant pinned digest already available locally; full stack not started |
| Caddy configuration | Validated | Pinned image, isolated network, dummy hostname/contact; no public certificate issuance tested |
| Special-character secrets | Preserved | Disposable container **created but not started**, inspected with dummy values; test exposed quoting differences, then verified `format: raw` correction |
| Azure policy/quota/image | Read-only verified | Austria East permitted; 2 cores within total/family quota; exact Ubuntu image and SKU checked; capacity allocation not guaranteed |
| Storage name | Available at check time | `stfikrahbackup2310`; availability can change |
| Billable Azure resources | None created | Resource inventory empty at scaffold check; only provider registration performed |
| Azure what-if/provisioning | Not run | Separate provisioning approval and operator inputs required |
| API/public HTTPS/live queues | Not run on Azure | Requires actual deployment and approved configuration |
| Real backup upload/restore | Not run | Mocked block-upload tests are not Azure transfer or recovery evidence |
| Capacity/latency/reboot | Not run on Azure | 4 GiB and worker limits are initial settings, not a benchmark |

## Review findings corrected before handover

- PostgreSQL readiness now probes TCP, avoiding its entrypoint's temporary Unix-socket server.
- Backup capture rejects unresolved prior uploads and insufficient conservative disk headroom; incomplete compressed files are removed.
- Private config output is forbidden in tracked repository paths, and source `${...}` interpolation is disabled.
- Runtime env files use raw Compose parsing to preserve credentials exactly.
- Recovery trap is installed before an upgrade backup can stop writers.
- Release packaging exports an explicit flat runtime allowlist, excluding tests and local files.
- Blob block requests include the required UTC date header; final blob commit is conditional and memory is bounded per block.
- Operator identity email was removed from the tracked parameters template.

No readiness result above implies high availability or production acceptance. See the [operator runbook](operations-runbook.md) for the remaining deployment and recovery gates.
