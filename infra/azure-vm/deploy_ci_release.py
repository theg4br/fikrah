#!/usr/bin/env python3
"""Render a nonsecret Azure Run Command and require an actual application success marker."""

import argparse
import json
import re
import subprocess
import textwrap
from pathlib import Path


def render_script(repository: str, revision: str, digest: str) -> str:
    if repository != "theg4br/fikrah":
        raise ValueError("Unexpected repository")
    if not re.fullmatch(r"[0-9a-f]{40}", revision) or not re.fullmatch(r"[0-9a-f]{64}", digest):
        raise ValueError("Invalid release revision or checksum")
    # No Azure/GitHub credential, app environment value or SSH key enters ARM history.
    return "#!/bin/bash\nset -eu\numask 077\npython3 - <<'FIKRAH_DEPLOY'\n" + textwrap.dedent(f'''
        import hashlib,json,shutil,subprocess,tarfile,tempfile,urllib.request
        from pathlib import Path
        revision={revision!r}
        digest={digest!r}
        current=Path('/opt/fikrah/current')
        # API is intentionally not published on the host; check it via Compose.
        def check_api(root):
            prefix=['docker','compose','--project-name','fikrah','--env-file','/etc/fikrah/compose.env',
                    '--env-file',str(root/'release.env'),'--file',str(root/'compose.yaml')]
            return subprocess.run(prefix+['exec','-T','api','python','-c',
                "import json,urllib.request; r=urllib.request.urlopen('http://127.0.0.1:8000/ready',timeout=10); assert r.status==200 and json.load(r)['status']=='ready'"],
                stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL).returncode==0
        if current.is_symlink() and json.loads((current/'release.json').read_text())['revision']==revision:
            if not check_api(current):raise RuntimeError('Existing release is not ready; manual recovery required')
            print('FIKRAH_DEPLOY_OK '+revision,flush=True)
            raise SystemExit(0)
        target=Path('/opt/fikrah/releases')/revision
        if target.exists():raise RuntimeError('Incomplete release exists; inspect protected deployment logs before retrying')
        work=Path(tempfile.mkdtemp(prefix='fikrah-deploy-',dir='/var/lib/fikrah'))
        try:
            archive=work/'release.tar.gz'
            url='https://github.com/{repository}/releases/download/demo-'+revision+'/fikrah-release.tar.gz'
            with urllib.request.urlopen(url,timeout=120) as response,archive.open('xb') as output:
                shutil.copyfileobj(response,output,length=1024*1024)
            with archive.open('rb') as source:
                if hashlib.file_digest(source,'sha256').hexdigest()!=digest:raise RuntimeError('Release digest mismatch')
            stage=work/'release';stage.mkdir()
            with tarfile.open(archive,'r:gz') as bundle:
                members=bundle.getmembers()
                expected={{'compose.yaml','Caddyfile','postgres-init.sh','bootstrap.sh','install_runtime.sh','install_release.sh','release.env','release.json','image.tar.gz'}}
                if {{item.name for item in members}}!=expected or len(members)!=len(expected):raise RuntimeError('Unexpected release members')
                for item in members:
                    if not item.isfile() or item.size>2*1024**3:raise RuntimeError('Unsafe release member')
                bundle.extractall(stage,filter='data')
            if json.loads((stage/'release.json').read_text())['revision']!=revision:raise RuntimeError('Release revision mismatch')
            logdir=Path('/var/log/fikrah-deploy');logdir.mkdir(mode=0o700,exist_ok=True)
            with (logdir/(revision+'.log')).open('ab') as log:
                result=subprocess.run(['bash',str(stage/'install_release.sh'),str(stage)],stdout=log,stderr=log)
            if result.returncode or not check_api(current):raise RuntimeError('Deployment failed; inspect protected host log; no database rollback attempted')
            print('FIKRAH_DEPLOY_OK '+revision,flush=True)
        finally:
            shutil.rmtree(work)
    ''') + "\nFIKRAH_DEPLOY\n"


def deploy(args: argparse.Namespace) -> None:
    digest_line = args.checksums.read_text().strip().split()
    if len(digest_line) != 2 or digest_line[1] != "fikrah-release.tar.gz":
        raise ValueError("Unexpected checksum file")
    script = render_script(args.repository, args.revision, digest_line[0])
    args.script.write_text(script)
    result = subprocess.run([
        "az", "vm", "run-command", "invoke", "--subscription", args.subscription,
        "--resource-group", args.resource_group, "--name", args.vm,
        "--command-id", "RunShellScript", "--scripts", "@" + str(args.script), "-o", "json",
    ], check=True, capture_output=True, text=True, timeout=1200)
    response = json.loads(result.stdout)
    messages = "\n".join(item.get("message", "") for item in response.get("value", []))
    if "FIKRAH_DEPLOY_OK " + args.revision not in messages:
        # Azure may return exit 0 even when the guest script failed.
        raise RuntimeError("Guest deployment did not report application readiness; inspect Azure Run Command and protected host logs")
    print("Deployment and application readiness confirmed for " + args.revision)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("repository", "revision", "subscription", "resource-group", "vm"):
        parser.add_argument("--" + name, required=True)
    parser.add_argument("--checksums", type=Path, required=True)
    parser.add_argument("--script", type=Path, required=True)
    deploy(parser.parse_args())


if __name__ == "__main__":
    main()
