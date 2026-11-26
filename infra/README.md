# Azure VM deployment scaffold

Start with the [deployment design](../docs/features/azure-vm-deployment/deployment-design.md), then follow the [operator runbook](../docs/features/azure-vm-deployment/operations-runbook.md).

`main.bicep` is subscription scoped. `main.parameters.json` intentionally omits the operator SSH key, caller `/32` and DNS label. Render these into ignored private state with `azure-vm/prepare_parameters.py`. Do not deploy until the concrete plan and costs are approved.

`azure-vm/compose.yaml` is standalone production Compose; do not combine it with the root development Compose file. Runtime credentials belong under `/etc/fikrah` on the host and are excluded from release bundles. The local development UI is not deployed.

Offline checks, using the existing project virtualenv:

```bash
ruff check infra
pytest -q --confcutdir=infra/azure-vm/tests infra/azure-vm/tests
az bicep build --file infra/main.bicep --stdout >/dev/null
```

The tests use dummy credentials and Compose config parsing; they do not start the production stack, provision Azure resources or prove application acceptance. Shell scripts must also pass `bash -n`/`sh -n` before use.
