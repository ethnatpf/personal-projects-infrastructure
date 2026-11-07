This repository contains the Ansible setup for self-hosting my home lab on a VPS. It deploys an observability stack with Grafana, a MongoDB database with backups, and my personal projects, including the Immersion Tracker app.

## Bootstrap

Before setting up a fresh Ubuntu VPS, follow the [bootstrap guide](documentation/bootstrap.md). It covers creating the `deploy` user, configuring SSH key authentication, and verifying Ansible access.

## Set up the VPS

After bootstrapping a fresh VPS, follow the [VPS setup guide](documentation/vps-setup.md) to configure security, authenticate Tailscale, and deploy the services.

## VPN access

Some applications hosted on the VPS are not publicly accessible (grafana is one of them). They are expected to be accessed through a VPN that is automatically installed by the site playbook.
After authenticating the VPS during setup, connect your local computer to Tailscale:

### Authenticate on your local machine

Once tailscale authenticated on the VPS, install tailscale on your local computer. Installation steps vary based on the OS.
Once installed, run the same command as above on your local computer, and authenticate again.

### Test the connection

On the VPS, run

```bash
tailscale ip -4
```

This will display your VPS tailscale IP

From your local machine, try to ping this IP with tailscale:

```bash
tailscale ping YOUR_VPS_TAILSCALE_IP
```

If successful, your computer and VPS can communicate over the private network.

## Grafana

Grafana is deployed automatically by the site playbook as part of the observability stack.

It is only accessible through Tailscale, so your local machine must be connected to the VPN before accessing it.

### 1. Access Grafana

Once connected to Tailscale, open the following URL in your browser:

```text
http://{VPS_TAILSCALE_IP}:3000
```

You can retrieve the VPS's Tailscale IP by running:

```bash
tailscale ip -4
```

### 2. Get the initial admin password

Grafana's initial administrator credentials are:

- **Username:** `admin`
- **Password:** Automatically generated during the first Ansible deployment.

To retrieve the password, SSH into your VPS and run:

```bash
sudo cat /opt/observability/grafana-admin-password
```

The password is generated only once and stored on the VPS. Subsequent Ansible runs will not regenerate it.

**Note:** The password file is used to initialize Grafana's admin account. Once Grafana has initialized its database, changing this file does not automatically change the account password.

### 4. Troubleshooting login issues

If the generated password doesn't work, reset the Grafana admin password using the stored password:

```bash
cd /opt/observability

sudo docker compose exec -u 0 grafana \
  grafana cli admin reset-admin-password \
  "$(sudo cat /opt/observability/grafana-admin-password)"
```

This resets the admin password without deleting Grafana's dashboards, users, or stored configuration.

If Grafana fails to start, inspect its logs:

```bash
sudo docker compose -f /opt/observability/compose.yml logs --tail 50 grafana
```
