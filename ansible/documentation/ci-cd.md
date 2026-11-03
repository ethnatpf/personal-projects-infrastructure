# CI/CD deployment workflow

## Why deployments are pull-based

I chose to have the VPS pull new application images instead of having GitHub Actions connect to the VPS. GitHub Actions can test, build, and publish images to GHCR, but it does not receive SSH access to the server or access to the infrastructure repository. This avoids giving CI credentials that could become a vulnerable entry point to the VPS.

This workflow is for my personal projects, including Immersion Tracker and its backend and frontend. Each application has its own image and deployment configuration, and an update applies only that application. The watcher reads a list in `inventory/group_vars/all/personal_project_deployments.yml`, so another project can be added there with its own image variable and dedicated playbook.

Each list entry names the GHCR image and watched tag, the image variable file and variable, the dedicated playbook, the Compose service and directory, and the health-check timeout. Each entry gets its own timer and persistent failure history; a shared lock serializes Git and deployment operations.

The GHCR packages are public, so the VPS can check and pull images without registry credentials.

## How an update works

1. GitHub Actions publishes an image to GHCR and updates its moving `stable` tag.
2. A systemd timer on the VPS checks GHCR every five minutes and resolves `stable` to an immutable image digest.
3. If the digest is already deployed, or has failed before, the VPS skips it. Otherwise, it fast-forward pulls the infrastructure repository and updates that application's image variable to the digest.
4. A dedicated Ansible playbook deploys only that application. The VPS waits for its container health check to pass.
5. On success, the VPS commits and pushes the image-variable change. If GitHub is temporarily unavailable, the deployed state is retained locally and Git synchronization is retried later.
6. If deployment or health verification fails, the VPS restores the previous image and verifies its health. The failed digest is saved locally and will not be retried automatically.

Failed digests remain on the VPS across reboots. After fixing or intentionally retrying a release, remove its digest from that application's failure history on the VPS:

```bash
sudo /usr/local/sbin/personal-project-deploy clear-failed immersion-tracker-backend sha256:<digest>
```

## GitHub credentials on the VPS

The VPS uses a fine-grained personal access token (PAT) over HTTPS to push successful image updates to the infrastructure repository. The token should be limited to this repository and granted only the contents permission needed to push. It is stored in Ansible Vault and installed in a root-only file on the VPS. Git reads it through a small askpass helper; the token is not placed in the remote URL, command arguments, or logs. GitHub Actions does not receive this token.

Set the token in Ansible Vault as `vault_personal_project_deploy_github_pat`. Give the token an expiry and rotate it before it expires.

The deployment role also installs `vault_personal_project_ansible_vault_password` as a root-only password file on the VPS, so scheduled local Ansible runs can decrypt Vault variables without prompting. Keep it equal to the password used to encrypt `vault.yml`, including after rekeying.

## Checking deployment status

The timer and service are managed by Ansible. Check their status and recent logs on the VPS with:

```bash
systemctl status personal-project-deploy@immersion-tracker-backend.timer
systemctl status personal-project-deploy@immersion-tracker-backend.service
journalctl -u personal-project-deploy@immersion-tracker-backend.service
```

Deployment service logs are also sent from journald to Loki with `service="personal-project-deploy"` and a `unit` label. In Grafana, query `{service="personal-project-deploy"}` for all project deployments, or filter by unit, for example `{service="personal-project-deploy", unit="personal-project-deploy@immersion-tracker-backend.service"}`.
