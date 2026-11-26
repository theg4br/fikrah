# Deployment readiness: initial Azure VM

## Objective

Prepare a reviewable, low-cost container deployment while preserving the backend contract and local unfinished work.

## Confirmed scope

Single Azure VM, Docker Compose, Austria East among subscription-permitted regions. Exact resource names, SKU, network access and backup policy are described in the [deployment design](../../features/azure-vm-deployment/deployment-design.md).

## Delivered scaffold

Bicep modules, standalone production Compose, private configuration generation, host bootstrap, immutable release packaging/installation, pre-migration backup and an operator recovery runbook. Application source, existing migrations and local `.env` are untouched.

## Gates

1. Static/offline validation and review, with no billable resources provisioned.
2. Separate subscription, cost and provisioning approval.
3. Azure validation/what-if and actual deployment.
4. Real HTTPS/API/queue/LLM/email acceptance, isolated restore drill, reboot recovery and capacity/latency measurements.

Remaining operator inputs are listed in the [runbook](../../features/azure-vm-deployment/operations-runbook.md). Production email-action routing/platform identifiers remain subject to user requirements. Backup timer activation requires an approved maintenance window and successful recovery evidence.
