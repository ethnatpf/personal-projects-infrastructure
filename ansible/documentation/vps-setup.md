# Set up the VPS

Once [bootstrap](bootstrap.md) is complete, use this guide to configure and deploy the VPS. The initial setup applies the security, Docker, Caddy, and Tailscale (VPN) roles, then deploys the observability and application containers.

## 1. Configure Ansible Vault

From the `ansible` directory, create the vault file used for secrets:

```bash
ansible-vault create inventory/group_vars/vault.yml
```

Add the required passwords and Discord webhook URL to the file:

```yaml
vault_mongodb_root_password: "your-mongodb-root-password"
vault_mongodb_immersion_tracker_user_password: "your-immersion-tracker-database-user-password"
vault_mongodb_exporter_password: "your-mongodb-exporter-user-password"
vault_mongodb_backup_password: "your-mongodb-backup-user-password"

# Use the same value as the password that encrypts vault.yml. The deployment
# role installs this value as a root-only file for unattended playbook runs (for CI/CD automations)
vault_personal_project_ansible_vault_password: "your-ansible-vault-password"

# Fine-grained PAT for deployment commits to this infrastructure repository.
# Limit it to this repository with Contents read/write permission and set an expiry.
vault_personal_project_deploy_github_pat: "github_pat_..."

vault_discord_webhook_url: "https://discord.com/api/webhooks/..."

# Hourly backups bucket S3 creds
vault_mongodb_backup_hourly_access_key: "..."
vault_mongodb_backup_hourly_secret_key: "..."

# Daily backups bucket S3 creds
vault_mongodb_backup_daily_access_key: "..."
vault_mongodb_backup_daily_secret_key: "..."

# Token to authenticate the metrics endpoint of the immersion tracker app
immersion_tracker_metrics_token: "..."

# Auth0 credentials used by the Immersion Tracker frontend
vault_immersion_tracker_auth0_client_id: "..."
vault_immersion_tracker_auth0_client_secret: "..."
vault_immersion_tracker_auth0_secret: "..."
```

Replace the example values with your own secrets. Keep the vault password available; Ansible will prompt for it when you apply the configuration.

## 2. Apply the security, Docker, Caddy, and Tailscale configuration

**Warning:** SSH hardening disables password authentication and direct root SSH login. Make sure SSH key authentication works before continuing.

Keep an existing SSH session open while applying security changes.

```bash
ansible-playbook playbooks/configure-vps.yml --ask-vault-pass --tags security,docker,caddy,tailscale
```

The security role configures:

- **UFW firewall:** Blocks incoming connections by default, except SSH (22), HTTP (80), and HTTPS (443).
- **SSH hardening:** Disables root SSH login and password authentication while keeping public-key authentication enabled.
- **Security packages:** Installs UFW and unattended-upgrades.

## 3. Verify the security configuration

On the VPS, check the firewall:

```bash
sudo ufw status verbose
```

It should report `Status: active`.

Validate the SSH configuration:

```bash
sudo sshd -t
```

If the previous command doesn't print anything, the configuration is valid.

Check the effective SSH authentication settings:

```bash
sudo sshd -T | grep -E '^(permitrootlogin|passwordauthentication|kbdinteractiveauthentication|pubkeyauthentication) '
```

Expected values:

```text
permitrootlogin no
pubkeyauthentication yes
passwordauthentication no
kbdinteractiveauthentication no
```

Finally, **open a new terminal** and confirm you can still connect using your SSH key:

```bash
ssh -i ~/.ssh/{KEY_NAME} deploy@{VPS_IP}
```

Do not close your existing SSH session until the new connection succeeds.

## 4. Authenticate Tailscale on the VPS

Tailscale is installed by the playbook, but it needs to be authenticated before the VPS can join your private network. On the VPS, run:

```bash
sudo tailscale up
```

Tailscale will provide an authentication URL. Open it in your browser and sign in.

## 5. Deploy the containers

Run the playbook with the application and service tags to deploy the containers on the VPS:

```bash
ansible-playbook playbooks/configure-vps.yml --ask-vault-pass --tags observability,mongodb,mongodb_backup,immersion_tracker,personal_project_deployer
```

Everything should now be up and running. On the VPS, check the container status:

```bash
sudo docker ps
```

You can also check Grafana's recent logs for errors:

```bash
sudo docker compose -f /opt/observability/compose.yml logs --tail 50 grafana
```

## Running the playbook

Ansible is idempotent: running the same playbook again should not make unnecessary changes.

To preview changes without applying them:

```bash
ansible-playbook playbooks/configure-vps.yml --check --diff --ask-vault-pass
```

To apply all configuration:

```bash
ansible-playbook playbooks/configure-vps.yml --ask-vault-pass
```
