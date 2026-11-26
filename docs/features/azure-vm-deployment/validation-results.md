# Demo deployment validation

## Observed results

| Check | Result | Scope |
| --- | --- | --- |
| Bicep compilation | Passed | Final demo template; no archive storage module |
| Runtime safety suite | 15 passed | Final simplified scripts; dummy credentials and offline Compose checks |
| Backend suite | 593 passed, 46 skipped | Existing virtualenv; optional infrastructure test environments not supplied; one Google SDK deprecation warning |
| Ruff | Passed | `ruff check app tests scripts infra`; four pre-existing I001 failures in historical migrations remain outside that scope |
| Shell syntax | Passed | Bootstrap, installers and packaging |
| Azure provisioning | Succeeded | Austria East B2als_v2, 64 GiB SSD, static IP and restricted network |
| Containers | Passed | All ten services running; four datastore/API health checks healthy, zero restarts/OOM in the last recorded check |
| Public HTTPS | Passed | Trusted certificate, `/health`, `/ready`, OpenAPI; no local UI dependency |
| OTP registration / SMTP | Passed with limitation | Registration and SMTP acceptance exercised. Verification used an internal challenge fixture for an owned demo account, not an automated Gmail inbox read |
| Login / refresh / logout | Passed | Protected account access, token rotation and access revocation |
| Notes | Passed | Idempotent create, real Celery processing, generated title, favorite and project cleanup |
| Search / graph / chat | Passed | Semantic retrieval, graph retrieval, SSE completion with grounded sources and saved history |
| Processing timing | About 6 seconds | One short test note; not a load benchmark |
| Streaming timing | First delta 1.26 seconds | Server-side HTTPS through Caddy using hostname/SNI and loopback routing; not end-user mobile latency |
| Backup removal | Passed | User changed scope to a college demo. Host jobs, scripts, archives and dedicated Azure storage removed; application data volumes retained |

The application image remains `fikrah:56166c968d1f254faff35153a61f736d39389e86`. The simplified host scripts are installed separately; release manifests describe the original packaged bytes, not later administrative removal of the backup tools.

## Fixes made during deployment

- Kept PostgreSQL's nonsecret initialization script readable by its container UID.
- Raised initial Celery memory caps after a scheduler OOM; running services total 3360 MiB in configured caps, leaving host headroom.
- Moved scheduler PID/schedule files to writable `/tmp`.
- Validated Caddy configuration without colliding with the live proxy IP.
- Selected the proxy image explicitly instead of receiving dependency image names.
- Preserved special characters in private runtime values and kept local files outside packaged images.

## Limits and remaining work

- No representative concurrency/load test, VM reboot rehearsal or real Flutter device acceptance yet.
- Google requires a real mobile Google ID token for end-to-end verification; audio transcription was not part of this deployment acceptance run.
- SMTP acceptance is not proof of inbox placement. Initial auth emails currently await SMTP inline, which can add auth latency; this existing application behavior was not refactored during deployment.
- Legacy email-action links remain intentionally unconfigured; use OTP.
- Single VM, no backups, no autoscaling or external alert recipient. These match the user's demo scope.
