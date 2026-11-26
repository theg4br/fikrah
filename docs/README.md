# FIKRAH documentation

FIKRAH is a Flutter-first private knowledge application. The backend owns authentication, account isolation, notes/projects, LLM processing, semantic search, graph edges and grounded chat. Flutter owns presentation and device integration.

| Need | Maintained reference |
| --- | --- |
| Product/user journeys and all API operations | [Mobile integration handbook](mobile/README.md) |
| Request/response schemas | [OpenAPI snapshot](mobile/openapi.json), or `/openapi.json` on the deployed API |
| Layer responsibilities and dependency boundaries | [Backend architecture](architecture/layered-modular-architecture.md) |
| Runtime environment variables and defaults | [Configuration inventory](features/local-environment-configuration/overview.md) |
| CI checks and main-only deployment | [Continuous delivery](features/continuous-delivery/main-only-deployment.md) |
| Azure VM commands, HTTPS and demo limitations | [Operator runbook](features/azure-vm-deployment/operations-runbook.md) |
| Liveness and dependency readiness | [Health checks](operations/health-checks.md) |
| Authentication and OTP | [Authentication](features/authentication/overview.md), [OTP](features/authentication/email-otp.md) |
| Queues and safe worker rollout | [Celery operations](features/celery-work-isolation/queues-and-rollout.md) |
| Security and performance decisions/debt | [Security](phases/security-hardening/security-remediation.md), [performance](phases/performance-hardening/performance-remediation.md) |

`docs/features/` retains current behavior and operational decisions that are not fully captured by OpenAPI. Historical foundation plans, repetitive internal-move reports and overlapping local-Compose guides were removed. Their useful boundaries are covered by the references above; Git history retains their previous content.

Do not add proposed capabilities to the implemented mobile reference. Update contracts and the OpenAPI snapshot together when behavior changes.
