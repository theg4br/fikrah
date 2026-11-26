# Azure demo deployment phase

## Scope

Run the existing backend and its data services together on one low-cost Azure VM in Austria East. Preserve the Flutter JSON/Bearer, OTP, note polling, idempotency and streaming contracts.

## User decisions

- One VM with Docker Compose; no Container Apps or production web frontend.
- OTP email verification/recovery; legacy app links remain unconfigured.
- College demo scope: remove backup jobs, archive storage and pre-upgrade backup requirements. Keep current application data and the services required to run the demo.

See the [deployment design](../../features/azure-vm-deployment/deployment-design.md), [operations](../../features/azure-vm-deployment/operations-runbook.md) and [validation](../../features/azure-vm-deployment/validation-results.md).

## Continuous delivery

Changes integrate through `dev`; only successful `main` push builds deploy to the demo VM. See [main-only delivery](../../features/continuous-delivery/main-only-deployment.md).
