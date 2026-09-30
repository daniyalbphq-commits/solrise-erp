#!/usr/bin/env python3
"""Run a shell command on the Solrise EC2 host over AWS Systems Manager.

Port 22 is not reachable from every network that has the AWS credentials, but
SSM is: the instance role already carries AmazonSSMManagedInstanceCore, so
`send-command` reaches the host when `ssh ubuntu@<ip>` times out. That is what
this is for - a deploy or a diagnosis when SSH is not an option.

    scripts/host-run.py 'uptime'
    scripts/host-run.py --as-ubuntu 'podman ps'
    scripts/host-run.py --timeout 1800 --file infra/ansible/... /dev/null

The command runs as root unless --as-ubuntu is given; the containers are rootless
and live in ubuntu's user session, so anything touching `podman` must use
--as-ubuntu. Exit status mirrors the remote one: a non-zero remote exit, or a
failed/aborted command, exits non-zero here.

This is a convenience wrapper around `aws ssm send-command`. It exists because
`aws ssm send-command` itself is unusable with this CLI build: it aborts with
"badly formed help string" before it ever reaches AWS (its --parameters help text
trips the argument parser), which is why this goes through boto3 directly.
"""
from __future__ import annotations

import argparse
import sys
import time

import boto3

INSTANCE_ID = "i-01c42248edf12fd9e"
REGION = "us-east-1"
TERMINAL = {"Success", "Failed", "Cancelled", "TimedOut"}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("command", help="shell snippet to run on the host")
    parser.add_argument(
        "--as-ubuntu",
        action="store_true",
        help="run inside ubuntu's login shell (needed for podman / the rootless stack)",
    )
    parser.add_argument("--instance", default=INSTANCE_ID)
    parser.add_argument("--region", default=REGION)
    parser.add_argument("--timeout", type=int, default=600, help="seconds to wait")
    parser.add_argument("--tail", type=int, default=400, help="lines of output to show")
    args = parser.parse_args()

    command = args.command
    if args.as_ubuntu:
        # XDG_RUNTIME_DIR is what finds the rootless podman socket; a login shell
        # alone is not enough under send-command, which has no user session.
        command = (
            "sudo -u ubuntu -i bash -lc "
            + _quote("export XDG_RUNTIME_DIR=/run/user/1000; " + command)
        )

    ssm = boto3.client("ssm", region_name=args.region)
    # The document echoes the snippet, so the output shows what actually ran.
    response = ssm.send_command(
        InstanceIds=[args.instance],
        DocumentName="AWS-RunShellScript",
        # The document runs the snippet with `sh`, which is dash here: no
        # `set -o pipefail` and no bashisms. Keep the snippet portable.
        Parameters={"commands": [command]},
        TimeoutSeconds=min(args.timeout, 2592000),
        Comment="solrise host-run",
    )
    command_id = response["Command"]["CommandId"]

    deadline = time.time() + args.timeout
    invocation = {}
    while time.time() < deadline:
        invocation = ssm.get_command_invocation(
            CommandId=command_id, InstanceId=args.instance
        )
        if invocation["Status"] in TERMINAL:
            break
        time.sleep(2)
    else:
        print(f"host-run: timed out after {args.timeout}s (command {command_id})",
              file=sys.stderr)
        return 124

    if invocation.get("StandardOutputContent"):
        print(_tail(invocation["StandardOutputContent"], args.tail))
    if invocation.get("StandardErrorContent"):
        print("--- stderr ---", file=sys.stderr)
        print(_tail(invocation["StandardErrorContent"], args.tail), file=sys.stderr)

    status = invocation["Status"]
    code = invocation.get("ResponseCode", 1)
    print(f"[host-run] {status} (exit {code})", file=sys.stderr)
    return 0 if status == "Success" and code == 0 else 1


def _quote(value: str) -> str:
    return "'" + value.replace("'", "'\"'\"'") + "'"


def _tail(text: str, lines: int) -> str:
    parts = text.rstrip("\n").split("\n")
    if len(parts) <= lines:
        return "\n".join(parts)
    return "\n".join([f"... ({len(parts) - lines} earlier line(s) omitted)"] +
                     parts[-lines:])


if __name__ == "__main__":
    raise SystemExit(main())
