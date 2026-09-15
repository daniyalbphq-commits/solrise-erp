# Deploying Solrise ERP on AWS (EC2 + RDS MariaDB) with Terraform and Ansible

This directory is the AWS deployment path from the main README: the application
runs on **EC2**, the database is **MariaDB on RDS**, infrastructure is
provisioned by **Terraform**, and the host is configured and deployed by
**Ansible**.

The image is built **once in GitHub Actions and pushed to Docker Hub** — the slow
bake (frappe + erpnext + hrms + cloud_storage, ~15 min, memory-hungry) never runs
on the production host. Ansible only pulls it.

> **Read [`PREREQUISITES.md`](PREREQUISITES.md) first.** It lists every account,
> key, credential and GitHub setting you need before `terraform init` and before
> `ansible-playbook`, plus the minimum IAM policies.

The in-repo VPS path (`docs/03-phase3-production-vps.md`) runs everything on one
host with MariaDB in a container. This path replaces only the database container
with managed RDS — the app tier is otherwise identical.

## Image supply chain

```mermaid
flowchart LR
    subgraph Workstation
      TF[Terraform apply]
      AN[Ansible]
    end
    subgraph GitHub
      GHA[Actions: build-image]
    end
    subgraph Registry
      DH[(Docker Hub)]
    end
    subgraph AWS
      EC2[EC2]
      RDS[(RDS MariaDB)]
      SM[Secrets Manager]
    end
    TF -->|creates| EC2
    TF -->|creates| RDS
    TF -->|creates| SM
    GHA -->|docker push, token secret| DH
    AN -->|podman pull| DH
    EC2 --> RDS
    AN -->|reads password| SM
    AN --> EC2
```

## Division of labour

| Concern | Tool | Files |
|---|---|---|
| VPC, subnets, security groups | Terraform | `terraform/network.tf`, `terraform/security.tf` |
| EC2 + Elastic IP + IAM/SSM role | Terraform | `terraform/compute.tf`, `terraform/iam.tf` |
| RDS MariaDB + parameter group (utf8mb4) | Terraform | `terraform/rds.tf` |
| RDS master password (generated + rotated) | Terraform (AWS-managed) | `terraform/rds.tf` → Secrets Manager |
| ECR repository (optional) + S3 backup bucket + S3 media bucket | Terraform | `terraform/storage.tf` |
| Instance-role policy for the media bucket | Terraform | `terraform/iam.tf` |
| GitHub OIDC provider + CI push role (only needed for the optional ECR path) | Terraform | `terraform/github_oidc.tf` |
| Route 53 A record | Terraform | `terraform/dns.tf` |
| Terraform → Ansible handoff | Terraform | `terraform/outputs.tf` → `ansible/inventory/hosts.ini`, `ansible/group_vars/all/terraform.yml` |
| **Image build + push to Docker Hub** | **GitHub Actions** | `.github/workflows/build-image.yml`, `scripts/build-image.sh`, `scripts/push-image.sh` |
| cloud_storage app in the image + its patch | GitHub Actions | `apps.json`, `infra/image/` |
| OS hardening, rootless Podman, podman socket | Ansible | `ansible/roles/host` |
| `.env`, image pull, stack, site, systemd, backups | Ansible | `ansible/roles/solrise` |
| Site file storage on S3 (config, credential check, migration) | Ansible + scripts | `ansible/roles/solrise`, `scripts/setup-media.sh`, `scripts/configure_s3_media.py` |

## The app-side prerequisite

RDS is external, so the stack uses **`compose/compose.aws.yaml`** — identical to
`compose.prod.yaml` minus the `mariadb` service, with `DB_HOST`/`DB_PORT` read
from `.env` and site creation using the RDS master user. That is wired through the
rest of the repo:

- `SITE_ENV=aws` selects it (`scripts/lib.sh`), so `backup.sh`, `restore.sh`,
  `create-site.sh` and `run-python.sh` all work unchanged.
- `make aws-up` / `aws-down` / `aws-logs` are the Make targets.
- `scripts/create-site.sh` skips the "wait for mariadb" step when there is no
  embedded database.

## Prerequisites (summary)

Full detail in [`PREREQUISITES.md`](PREREQUISITES.md). In short, before
`terraform init` you need: an AWS account + region, AWS credentials for a
principal that can create the resources, an SSH key pair, a domain + ACME email,
and the GitHub repo slug for OIDC. Before Ansible you additionally need: AWS
credentials on the control node, the SSH private key, an Ansible Vault password,
and **the image already in ECR**.

## 1. Provision (Terraform)

```bash
cd infra/terraform
cp terraform.tfvars.example terraform.tfvars
$EDITOR terraform.tfvars          # domain, email, SSH key, github_repository, ssh_cidr_blocks
terraform init
terraform apply
```

`apply` writes two files consumed by Ansible:

- `infra/ansible/inventory/hosts.ini` — the host and its IP (gitignored)
- `infra/ansible/group_vars/all/terraform.yml` — RDS endpoint, secret ARN, domain, ECR URL, bucket

Record the outputs you need next:

```bash
terraform output github_actions_role_arn   # -> GitHub variable AWS_ROLE_ARN
terraform output ecr_repository_url        # -> GitHub variable ECR_REPOSITORY_URL
terraform output app_public_ip
terraform output rds_endpoint
```

> **Engine version.** Confirm RDS offers what you set:
> `aws rds describe-db-engine-versions --engine mariadb --query 'DBEngineVersions[].EngineVersion'`.
> `db_engine_version` and `db_parameter_group_family` must match (`11.4` ↔ `mariadb11.4`).

## 2. Build the image in CI (GitHub Actions)

Set the repository secrets from `PREREQUISITES.md` §4 (`DOCKERHUB_USERNAME`,
`DOCKERHUB_TOKEN`), then either push to `main` or run the workflow by hand:

```bash
gh workflow run build-image.yml -f tag=version-15
gh run watch
```

It logs in to Docker Hub with the token, builds `scripts/build-image.sh` and
pushes `scripts/push-image.sh`. The image name follows the token's account unless
you set the `CUSTOM_IMAGE` variable, so the default result is:

```
docker.io/<DOCKERHUB_USERNAME>/solrise:version-15
```

`CUSTOM_TAG` (GitHub) must equal `custom_tag`, and the effective image name must
equal `custom_image`, in `infra/ansible/group_vars/all/main.yml` - otherwise the
host pulls something CI never pushed. A push to `main` touching `apps.json`,
`infra/image/**` or the build scripts triggers a rebuild automatically.

> Offline fallback: empty `custom_image` in `group_vars/all/main.yml` and
> `rebuild_image: true` builds on the EC2 host instead (also set
> `enable_ecr = false` unless you want the Terraform-created ECR repository).
> This reintroduces the ~15-minute host build and is not the default.
>
> ECR is still supported: leave `custom_image` empty and Terraform's
> `ecr_repository_url` is used instead (the CI workflow no longer pushes there,
> so you would push with `./scripts/push-image.sh` from a logged-in host).

## 3. Secrets (Ansible Vault)

The RDS master password is **not** in Vault or in `.tfvars` — AWS generates it,
rotates it, and stores it in Secrets Manager. You only supply the Frappe
`Administrator` password:

```bash
cd infra/ansible
cp group_vars/all/vault.example.yml group_vars/all/vault.yml
$EDITOR group_vars/all/vault.yml          # set vault_admin_password
ansible-vault encrypt group_vars/all/vault.yml
```

Point `repo_url`, `solrise_app_url` and branches at your repositories in
`group_vars/all/main.yml`.

## 4. Configure and deploy (Ansible)

```bash
cd infra/ansible
ansible-galaxy collection install -r requirements.yml
ansible-playbook site.yml --ask-vault-pass
```

The playbook:

1. installs podman, `podman-compose`, git, `awscli`, ufw, fail2ban; creates swap
   and the sysctl values Redis/Traefik/rootless Podman need;
2. enables lingering + the rootless `podman.socket`;
3. clones the repo to `/opt/solrise-erp`, renders `.env` from the Terraform facts
   and Vault (RDS endpoint, generated master password, domain, ECR image);
4. **logs in to ECR and pulls the image** (no build), brings the stack up
   (`make aws-up`), and creates the site — which reaches RDS with
   `DB_ROOT_USERNAME`/`DB_ROOT_PASSWORD`;
5. installs a **rootless user systemd unit** so the stack comes back after reboot,
   and a daily backup cron (optionally syncing files to S3);
6. gates on `GET https://<site>/api/method/ping` returning 200 (Let's Encrypt
   issuance is part of that wait).

Scope the run when iterating:

```bash
ansible-playbook site.yml --tags host   --ask-vault-pass  # OS prep only
ansible-playbook site.yml --tags deploy --ask-vault-pass  # redeploy only
```

## 5. Redeploying after a code change

- **App image changed** → push to `main` (or run the workflow), then:
  ```bash
  ansible-playbook site.yml --tags deploy --ask-vault-pass
  ```
- **Only config/scripts changed** → just re-run the `deploy` tag; `rebuild_image`
  is already `false`, so the image is not rebuilt.
- **Pin a rollback** → run the workflow with an older `tag`, set that tag in
  `group_vars/all/main.yml`, and re-run `deploy`. `PULL_POLICY=always` makes the
  host fetch the newly tagged image.

## 6. Uploaded files on S3 (media)

Attachments and images are stored in a **private, versioned S3 bucket** instead
of the EC2 volume. `terraform apply` creates the bucket and grants the instance
role access to it; the deploy installs the `cloud_storage` app on the site, points
it at the bucket (credentials come from the instance profile, so there is nothing
secret to manage) and proves it with a round trip. Files already on the volume are
migrated on request.

Full write-up, including the build-time patch the app needs, the migration
procedure, the backup implications and the caveats:
[`docs/15-s3-media-storage.md`](../docs/15-s3-media-storage.md).

```bash
terraform output media_bucket        # the bucket
SITE_ENV=aws make media              # re-apply the storage settings on the host
```

## 7. Migrating existing data onto RDS

The existing backup/restore path works because the restore talks to whatever
`db_host` points at:

```bash
# workstation, from the repo root
make backup                                    # ./backups/<stamp>/
scp -r backups/<stamp> ubuntu@<eip>:/tmp/solrise-backup

# on the EC2 host
cd /opt/solrise-erp
SITE_ENV=aws BACKUP_DIR=/tmp/solrise-backup ./scripts/restore.sh /tmp/solrise-backup/<stamp> --new
```

`DB_ROOT_USERNAME` and `DB_ROOT_PASSWORD` come from the rendered `.env`; the
restore creates the site and loads the dump into RDS. Public/private files land
on the EBS volume (and are what the S3 sync protects).

## 8. Day-two operations

| Task | Command |
|---|---|
| Stack status | `SITE_ENV=aws make ps` (or `make aws-logs`) |
| Shell on the host | `ssh ubuntu@<eip>` or `aws ssm start-session --target <instance-id>` |
| Bench shell | `podman exec -it solrise-backend bash` |
| Migrate | `podman exec -it solrise-backend bench --site <site> migrate` |
| DB snapshot / PITR | RDS console (automated, `db_backup_retention_days`) |
| File backups | `/opt/solrise-erp/backups` + optional `s3://<bucket>/files/` |
| Uploaded files (media) | `s3://<media bucket>/` - versioned, no expiry; migrate with `SITE_ENV=aws make media` |
| Rotate master password | Secrets Manager rotation on the RDS-managed secret |
| Traefik cert | `openssl s_client -connect <domain>:443 -servername <domain>` |
| Rebuild image | `gh workflow run build-image.yml` |

## 9. Teardown

`db_deletion_protection = true` and a final snapshot are on by default. For a
throwaway stack set `db_deletion_protection = false` and (optionally)
`db_skip_final_snapshot = true`, then:

```bash
cd infra/terraform && terraform destroy
```

The EBS volumes are deleted with the instance; RDS keeps its final snapshot. The
ECR repository is retained unless you remove it too.

## Known limitations / follow-ups

- The **rootless user systemd unit** is the one piece that needs verification on a
  live host: `systemctl --user status solrise` should show it active after
  `loginctl enable-linger`. (The repo previously relied only on container restart
  policies, which do not bring a rootless stack up at boot.)
- `awscli` comes from apt (v1); if your distro lacks the package, install the v2
  bundle and adjust the ECR login / S3 cron.
- The S3 backup sync is **off** by default (`backup_s3_enabled: false`); turn it on
  once you have confirmed credentials work on the host. It covers the *database*
  dump plus any files still on the volume - uploaded files live in the media
  bucket and are protected by its versioning instead
  (`docs/15-s3-media-storage.md`).
- The media bucket depends on a small build-time patch of `cloud_storage`
  (`infra/image/patch-cloud-storage.py`): instance-profile credentials and no
  LibreOffice. The image build fails if the pinned release no longer matches the
  patch, so bump the tag in `apps.json` deliberately, together with the patch.
- Read replicas are not wired into Frappe; the RDS endpoint is a single writer.
- The CI workflow triggers on `main` by default. Adjust the branch filter and
  `github_oidc_branch` together if you deploy from another branch.
