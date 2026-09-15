# Prerequisites — accounts, keys and credentials

Everything you must have **before** the first `terraform init` and **before** the
first Ansible run. Read the two checklists in order; the ordering matters because
the image has to exist in the registry before Ansible deploys.

The intended flow:

```
terraform apply  ->  GitHub Actions builds + pushes image to Docker Hub  ->  ansible-playbook pulls it
```

---

## 0. TL;DR checklist

| # | Item | Needed by | Where it goes |
|---|------|-----------|---------------|
| 1 | AWS account (billing enabled) | Terraform | — |
| 2 | AWS region chosen | Terraform, host | `terraform.tfvars`, `AWS_REGION` on the host |
| 3 | AWS credentials for the Terraform principal | Terraform | `AWS_PROFILE` / env / SSO |
| 4 | EC2 SSH key pair (public key, or existing key name) | Terraform | `tfvars: ssh_public_key` / `key_name` |
| 5 | Domain name + ACME email | Terraform, host | `tfvars: domain`, `letsencrypt_email` |
| 6 | Docker Hub account + image repository | CI | `daniyalbphq/solrise` (or set `CUSTOM_IMAGE`) |
| 7 | Docker Hub access token | CI | GitHub secret `DOCKERHUB_TOKEN` |
| 8 | Route 53 hosted zone ID (optional) | Terraform | `tfvars: route53_zone_id` |
| 9 | AWS credentials on the Ansible control node | Ansible | `AWS_PROFILE` / env |
| 10 | SSH **private** key | Ansible | `~/.ssh/...` |
| 11 | Ansible Vault password | Ansible | `--ask-vault-pass` |
| 12 | Image already pushed to Docker Hub | Ansible | produced by CI |

Items **1–8** must exist before `terraform apply` (and therefore before `init`
in practice). Items **9–12** must exist before `ansible-playbook`.

---

## 1. Accounts

### AWS account
- An AWS account with billing enabled. Record the **account ID** and pick a
  **region** you will use everywhere (Terraform, GitHub Actions, RDS, EC2).
- An **IAM principal for Terraform** — an IAM user with access keys, an assumed
  role, or IAM Identity Center (SSO) credentials. It must be able to create the
  resources in §2. The minimum policy is in §7.
- **Service quotas**: a fresh account usually allows one Elastic IP and one
  `db.t4g.medium`; raise RDS/EC2 quotas if you plan to scale.
- **Billing guard** (recommended): a budget alert before you `apply`.

> You do **not** need a pre-created VPC, subnet, ECR repo, IAM role, key pair in
> AWS, or an RDS instance. Terraform creates all of those.

### GitHub
- A GitHub account/organisation with **Actions enabled** on the repo that holds
  this project (`daniyalbphq-commits/solrise-erp`).
- A **Docker Hub account** with access to the image repository CI pushes to
  (`<DOCKERHUB_USERNAME>/solrise` unless you override `CUSTOM_IMAGE`), plus an
  access token for the workflow — see §4.
- The repository that holds the **custom app**, if `apps.json` references
  `${SOLRISE_APP_URL}`. If it is private, the build needs a token for it (see
  §4); the app list shipped here does not use it, so this is optional.
- No AWS OIDC setup is required for the image build: CI authenticates to Docker
  Hub, not to AWS.

### DNS
- A domain you control. Either a **Route 53 hosted zone** in the same account
  (then set `route53_zone_id`) or your registrar's DNS (then create the A record
  yourself after `apply`, pointing at the Elastic IP output).
- Use a subdomain such as `erp.example.com`.

---

## 2. What Terraform creates for you (no keys needed in advance)

So you know you are *not* missing anything for these:

| Resource | Purpose |
|---|---|
| RDS master password (Secrets Manager) | AWS-generated and rotated; never in a `.tfvars` or Vault |
| EC2 IAM role / instance profile | lets the host read that secret, reach S3 and (optionally) pull from ECR |
| S3 media bucket (+ bucket policy, versioning, lifecycle) | uploaded files, so they are not on the EC2 volume |
| S3 backup bucket | off-box copy of the database dumps |
| VPC, subnets, security groups, Elastic IP, Route 53 record | infrastructure |
| ECR repository, GitHub OIDC provider + CI role | **optional** - only for the ECR image path; CI pushes to Docker Hub by default |

---

## 3. Before `terraform init` / `apply`

### 3.1 Tooling on your workstation
| Tool | Minimum | Check |
|---|---|---|
| Terraform | 1.5 | `terraform version` |
| AWS CLI | v2 | `aws --version` |
| `git` | any recent | `git --version` |

### 3.2 AWS credentials in your environment
```bash
export AWS_PROFILE=my-terraform-profile     # or AWS_ACCESS_KEY_ID / _SECRET_ACCESS_KEY (+ _SESSION_TOKEN)
aws sts get-caller-identity                 # must succeed before you continue
```

### 3.3 Values to fill into `terraform.tfvars`
| Variable | Example | Notes |
|---|---|---|
| `aws_region` | `us-east-1` | use the same region everywhere |
| `domain` | `erp.example.com` | A record + Let's Encrypt |
| `letsencrypt_email` | `ops@example.com` | ACME contact |
| `ssh_public_key` | `ssh-ed25519 AAAA... you@laptop` | Terraform creates the key pair |
| `key_name` | `my-existing-key` | alternative to `ssh_public_key` |
| `ssh_private_key_path` | `~/.ssh/id_ed25519` | written into the Ansible inventory |
| `ssh_cidr_blocks` | `["203.0.113.4/32"]` | lock SSH to your egress IP |
| `enable_media_bucket` | `true` | S3 bucket for uploaded files (see `docs/15-s3-media-storage.md`) |
| `enable_ecr` | `false` | not needed: CI pushes to Docker Hub |
| `github_repository` | `acme/solrise-erp` | only needed for the optional OIDC/ECR path |
| `github_oidc_branch` | `refs/heads/main` | only this ref may push (optional path) |
| `create_github_oidc_provider` | `true` | set `false` if the account already has one |
| `route53_zone_id` | `Z0123...` | optional; omit to manage DNS yourself |
| `db_engine_version` / `db_parameter_group_family` | `11.4` / `mariadb11.4` | verify both are offered in your region |

Generate the SSH key if you need one:
```bash
ssh-keygen -t ed25519 -f ~/.ssh/solrise_ed25519 -C solrise
cat ~/.ssh/solrise_ed25519.pub      # paste into ssh_public_key
```

Verify the RDS engine/version exists before `apply`:
```bash
aws rds describe-db-engine-versions --engine mariadb \
  --query 'DBEngineVersions[].EngineVersion' --output table
```

### 3.4 Terraform state (recommended)
For anything shared or long-lived, use the S3 backend commented out in
`infra/terraform/versions.tf`. That needs a **state bucket** and (optionally) a
**DynamoDB lock table** created out of band, plus the permissions in §7
(`TerraformState`, `StateLock`).

### 3.5 Then
```bash
cd infra/terraform
terraform init
terraform apply
```

Record these outputs — you need them next:

```bash
terraform output app_public_ip
terraform output rds_endpoint
terraform output db_secret_arn
terraform output media_bucket      # empty when enable_media_bucket = false
```

Only for the optional ECR path: `terraform output ecr_repository_url` and
`terraform output github_actions_role_arn`.

---

## 4. Before the image can be built (Docker Hub + GitHub repository settings)

GitHub Actions builds the image and pushes it to Docker Hub. Two secrets are all
it needs.

### 4.1 Create the Docker Hub access token

Do **not** use your Docker Hub account password: use an access token, so it can
be revoked on its own and scoped to push/pull only.

1. Sign in at <https://hub.docker.com> as the account/org that will own the image
   (here: **`daniyalbphq`** — the same account as `docker.io/daniyalbphq/solrise`
   in `infra/ansible/group_vars/all/main.yml`).
2. Make sure the image repository exists:
   **Repositories → Create repository** → name `solrise`, visibility *Public*
   (a public repo lets the host pull without credentials; keep it private if you
   prefer, then also set the Vault credentials in §3/§5).
3. **Account Settings → Personal access tokens → Generate new token**
   (direct link: <https://hub.docker.com/settings/security>):
   - Description: `github-actions-solrise-erp`
   - Access permissions: **Read & Write** (add *Delete* only if you want CI to be
     able to remove tags)
   - Expiration: 90 days is a reasonable default; calendar the renewal
4. **Generate** → **Copy** the token (`dckr_pat_...`). It is shown once.

### 4.2 Add them to GitHub

Repository → **Settings → Secrets and variables → Actions → Secrets → New
repository secret** (the repository must already exist on GitHub):

| Secret | Value |
|---|---|
| `DOCKERHUB_USERNAME` | `daniyalbphq` (the account that owns the token) |
| `DOCKERHUB_TOKEN` | the `dckr_pat_...` token from §4.1 |
| `SOLRISE_APP_URL` | *optional*: `https://x-access-token:<PAT>@github.com/<owner>/<app>` — only if `apps.json` references `${SOLRISE_APP_URL}` and that repo is private |

Or with the `gh` CLI from a clone of the repo:

```bash
gh secret set DOCKERHUB_USERNAME --body daniyalbphq \
  --repo daniyalbphq-commits/solrise-erp
gh secret set DOCKERHUB_TOKEN --body "dckr_pat_xxxxxxxx" \
  --repo daniyalbphq-commits/solrise-erp
gh secret list --repo daniyalbphq-commits/solrise-erp   # verify: names only, never values
```

The workflow reads them as `secrets.DOCKERHUB_USERNAME` / `secrets.DOCKERHUB_TOKEN`,
logs in with `docker/login-action`, then runs `scripts/build-image.sh` and
`scripts/push-image.sh`.

### 4.3 Optional repository **variables** (defaults are built in)

Set these only to change the defaults; without them the workflow pushes
`docker.io/<DOCKERHUB_USERNAME>/solrise:version-15`.

| Variable | Default | Notes |
|---|---|---|
| `CUSTOM_IMAGE` | `docker.io/<DOCKERHUB_USERNAME>/solrise` | another namespace or a private registry |
| `CUSTOM_TAG` | `version-15` | **must match** `custom_tag` in `infra/ansible/group_vars/all/main.yml` |
| `FRAPPE_BRANCH` | `version-15` | must match `frappe_branch` on the host |
| `SOLRISE_APP_BRANCH` | `version-15` | branch of the custom app, when used |

`gh variable set CUSTOM_TAG --body version-15 --repo daniyalbphq-commits/solrise-erp`
works the same way.

> **Keep the tag in sync.** CI's effective image+tag and the host's
> `custom_image`/`custom_tag` must be identical, or `podman pull` on the host
> fetches an old (or missing) image.

> **Optional ECR path:** if you would rather keep images in AWS, leave
> `custom_image` empty in `group_vars/all/main.yml`, set `github_repository` in
> `terraform.tfvars`, and restore the OIDC/ECR steps in
> `.github/workflows/build-image.yml` from git history. Docker Hub is the
default and needs no AWS credentials.

---

## 5. Before Ansible

### 5.1 Tooling on the control node
| Tool | Minimum |
|---|---|
| `ansible-core` | 2.15 |
| `amazon.aws` collection | 7.0 (`ansible-galaxy collection install -r infra/ansible/requirements.yml`) |
| `community.general`, `ansible.posix` | per `requirements.yml` |
| `python3-boto3` / `botocore` | for the Secrets Manager lookup and the optional dynamic inventory |
| SSH client | any |

```bash
pip install --user ansible-core boto3 botocore
cd infra/ansible && ansible-galaxy collection install -r requirements.yml
```

### 5.2 AWS credentials on the control node
Ansible reads the RDS master password from **Secrets Manager**, so the control
node needs read access to that one secret (the Ansible *host* does not — it uses
its instance role):

```bash
export AWS_PROFILE=my-ansible-profile
```

Minimum permissions for that principal:
```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Sid": "ReadRdsSecret",
      "Effect": "Allow",
      "Action": ["secretsmanager:GetSecretValue", "secretsmanager:DescribeSecret"],
      "Resource": "<db_secret_arn from terraform output>"
    },
    {
      "Sid": "DynamicInventoryOptional",
      "Effect": "Allow",
      "Action": ["ec2:DescribeInstances", "ec2:DescribeTags"],
      "Resource": "*"
    }
  ]
}
```

### 5.3 SSH private key
The private half of the key whose public half you gave Terraform
(`ssh_private_key_path`), reachable from the control node. Confirm you can connect
before running the playbook:

```bash
ssh -i ~/.ssh/solrise_ed25519 ubuntu@$(terraform -chdir=infra/terraform output -raw app_public_ip)
```

### 5.4 Ansible Vault password
```bash
cd infra/ansible
cp group_vars/all/vault.example.yml group_vars/all/vault.yml
$EDITOR group_vars/all/vault.yml          # set vault_admin_password
ansible-vault encrypt group_vars/all/vault.yml
```
Keep the vault password itself in your password manager / CI secret store.

The same file holds the **Docker Hub** credentials the host uses to `podman
login` before pulling. They are optional for a *public* image, and worth setting
anyway: authenticated pulls are not subject to Docker Hub's anonymous rate
limits. Reuse the token from §4.1.

```yaml
dockerhub_username: "daniyalbphq"
dockerhub_password: "dckr_pat_..."    # personal access token, not the password
```

### 5.5 The image must already be in Docker Hub
Ansible **pulls**; it does not build by default. Trigger the build first:

```bash
gh workflow run build-image.yml -f tag=version-15     # or push to main
gh run watch
```

Then confirm the image is there — the workflow summary prints the exact
reference, and Docker Hub shows it under **Repositories → solrise → Tags**:

```bash
gh run list --workflow build-image.yml --limit 3
```

If the image is private, also set the Vault credentials in §5.4 so the host can
log in before pulling.

(Or build and push from your workstation — see §6.)

### 5.6 Then
```bash
cd infra/ansible
ansible-playbook site.yml --ask-vault-pass
```

---

## 6. Manual image push (no CI)

If you would rather build locally and push by hand:

```bash
# log in with the access token from §4.1
printf '%s\n' "$DOCKERHUB_TOKEN" | podman login --username daniyalbphq --password-stdin docker.io

# .env: CUSTOM_IMAGE=docker.io/daniyalbphq/solrise, CUSTOM_TAG=version-15,
#       FRAPPE_BRANCH=version-15, CONTAINER_ENGINE=podman
make image
./scripts/push-image.sh
```

No AWS credentials are involved: the push goes to Docker Hub with the token.

---

## 7. Minimum IAM policy for the Terraform principal

This is a practical starting point. It is broad in places (`ec2:Describe*`,
`rds:*`); scope it down to your organisation's standards. `AdministratorAccess`
works for a first bring-up but is not recommended for ongoing use.

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Sid": "TerraformState",
      "Effect": "Allow",
      "Action": [
        "s3:ListBucket", "s3:GetObject", "s3:PutObject", "s3:DeleteObject",
        "s3:GetBucketLocation", "s3:GetBucketVersioning"
      ],
      "Resource": [
        "arn:aws:s3:::my-tfstate-bucket",
        "arn:aws:s3:::my-tfstate-bucket/*"
      ]
    },
    {
      "Sid": "StateLock",
      "Effect": "Allow",
      "Action": [
        "dynamodb:GetItem", "dynamodb:PutItem", "dynamodb:DeleteItem", "dynamodb:DescribeTable"
      ],
      "Resource": ["arn:aws:dynamodb:us-east-1:123456789012:table/terraform-locks"]
    },
    {
      "Sid": "NetworkAndCompute",
      "Effect": "Allow",
      "Action": [
        "ec2:Describe*",
        "ec2:CreateVpc", "ec2:DeleteVpc", "ec2:ModifyVpcAttribute",
        "ec2:CreateSubnet", "ec2:DeleteSubnet", "ec2:ModifySubnetAttribute",
        "ec2:CreateInternetGateway", "ec2:DeleteInternetGateway",
        "ec2:AttachInternetGateway", "ec2:DetachInternetGateway",
        "ec2:CreateRouteTable", "ec2:DeleteRouteTable", "ec2:CreateRoute", "ec2:DeleteRoute",
        "ec2:AssociateRouteTable", "ec2:DisassociateRouteTable",
        "ec2:CreateSecurityGroup", "ec2:DeleteSecurityGroup",
        "ec2:AuthorizeSecurityGroupIngress", "ec2:AuthorizeSecurityGroupEgress",
        "ec2:RevokeSecurityGroupIngress", "ec2:RevokeSecurityGroupEgress",
        "ec2:RunInstances", "ec2:TerminateInstances", "ec2:StopInstances", "ec2:StartInstances",
        "ec2:CreateTags", "ec2:DeleteTags",
        "ec2:AllocateAddress", "ec2:ReleaseAddress", "ec2:AssociateAddress", "ec2:DisassociateAddress",
        "ec2:CreateKeyPair", "ec2:DeleteKeyPair", "ec2:ImportKeyPair",
        "ec2:CreateVolume", "ec2:DeleteVolume", "ec2:AttachVolume", "ec2:DetachVolume"
      ],
      "Resource": "*"
    },
    {
      "Sid": "Rds",
      "Effect": "Allow",
      "Action": ["rds:*"],
      "Resource": "*"
    },
    {
      "Sid": "IamForRolesAndOidc",
      "Effect": "Allow",
      "Action": [
        "iam:CreateRole", "iam:DeleteRole", "iam:GetRole", "iam:UpdateAssumeRolePolicy",
        "iam:TagRole", "iam:ListInstanceProfilesForRole",
        "iam:AttachRolePolicy", "iam:DetachRolePolicy", "iam:ListAttachedRolePolicies",
        "iam:PutRolePolicy", "iam:GetRolePolicy", "iam:DeleteRolePolicy", "iam:ListRolePolicies",
        "iam:CreatePolicy", "iam:DeletePolicy", "iam:GetPolicy", "iam:GetPolicyVersion",
        "iam:ListPolicyVersions", "iam:CreatePolicyVersion", "iam:DeletePolicyVersion",
        "iam:CreateInstanceProfile", "iam:DeleteInstanceProfile", "iam:GetInstanceProfile",
        "iam:AddRoleToInstanceProfile", "iam:RemoveRoleFromInstanceProfile",
        "iam:CreateOpenIDConnectProvider", "iam:DeleteOpenIDConnectProvider",
        "iam:GetOpenIDConnectProvider", "iam:TagOpenIDConnectProvider",
        "iam:PassRole"
      ],
      "Resource": "*"
    },
    {
      "Sid": "Ecr",
      "Effect": "Allow",
      "Action": [
        "ecr:CreateRepository", "ecr:DeleteRepository", "ecr:DescribeRepositories",
        "ecr:GetAuthorizationToken", "ecr:PutLifecyclePolicy", "ecr:GetLifecyclePolicy",
        "ecr:TagResource", "ecr:ListTagsForResource",
        "ecr:BatchCheckLayerAvailability", "ecr:InitiateLayerUpload", "ecr:UploadLayerPart",
        "ecr:CompleteLayerUpload", "ecr:PutImage", "ecr:BatchGetImage", "ecr:GetDownloadUrlForLayer"
      ],
      "Resource": "*"
    },
    {
      "Sid": "S3Backups",
      "Effect": "Allow",
      "Action": [
        "s3:CreateBucket", "s3:DeleteBucket", "s3:ListBucket",
        "s3:GetBucketLocation", "s3:GetBucketVersioning", "s3:PutBucketVersioning",
        "s3:GetBucketPublicAccessBlock", "s3:PutBucketPublicAccessBlock",
        "s3:GetEncryptionConfiguration", "s3:PutEncryptionConfiguration",
        "s3:GetLifecycleConfiguration", "s3:PutLifecycleConfiguration",
        "s3:GetBucketTagging", "s3:PutBucketTagging"
      ],
      "Resource": "*"
    },
    {
      "Sid": "SecretsManager",
      "Effect": "Allow",
      "Action": [
        "secretsmanager:CreateSecret", "secretsmanager:DeleteSecret", "secretsmanager:DescribeSecret",
        "secretsmanager:GetSecretValue", "secretsmanager:TagResource",
        "secretsmanager:PutResourcePolicy", "secretsmanager:GetResourcePolicy"
      ],
      "Resource": "*"
    },
    {
      "Sid": "Route53",
      "Effect": "Allow",
      "Action": [
        "route53:GetHostedZone", "route53:ListHostedZones", "route53:ListResourceRecordSets",
        "route53:ChangeResourceRecordSets", "route53:GetChange"
      ],
      "Resource": "*"
    },
    {
      "Sid": "Identity",
      "Effect": "Allow",
      "Action": ["sts:GetCallerIdentity"],
      "Resource": "*"
    }
  ]
}
```

---

## 8. Hygiene

- **No long-lived registry password.** CI authenticates to Docker Hub with a
  scoped access token (`DOCKERHUB_TOKEN`), never the account password, and the
  host uses a token too. Revoke a token to cut off a deployment.
- **No static AWS keys in GitHub.** The image build does not touch AWS at all.
  The only optional AWS-related secret is `SOLRISE_APP_URL` (a scoped GitHub PAT)
  when the custom app repo is private.
- **No DB password anywhere in your config.** RDS rotates it in Secrets Manager;
  the host reads it through its instance role, the control node through the
  policy in §5.2.
- **Encrypt the vault file** and keep the vault password out of the repo.
- **Restrict `ssh_cidr_blocks`** to your egress IP; prefer SSM Session Manager
  (`terraform output ssm_start_session_command`) over opening SSH at all.
- **Keep tags in sync.** CI's effective `CUSTOM_IMAGE:CUSTOM_TAG` and the host's
  `custom_image`/`custom_tag` must match, or `podman pull` fetches an old (or
  missing) image.
