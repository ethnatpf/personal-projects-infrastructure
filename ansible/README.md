## Bootstrap

Follow these steps to bootstrap a fresh VPS running Ubuntu.

The bootstrap playbook creates a `deploy` user, configures SSH key authentication, and grants administrative privileges through `sudo`.

### 1. Generate an SSH key

On your local machine:

```bash
ssh-keygen -t ed25519 -f ~/.ssh/{KEY_NAME} -C "{KEY_NAME}"
```

If your SSH key has a passphrase, load it into an SSH agent:

```bash
eval "$(ssh-agent -s)"
ssh-add ~/.ssh/{KEY_NAME}
```

Ansible cannot interactively prompt for an SSH key passphrase in the usual way. The SSH agent allows Ansible to use your unlocked key without removing its passphrase.

### 2. Configure the inventory

Copy `inventory/hosts.ini.example` to `inventory/hosts.ini` and fill in your VPS information.

Initially, configure Ansible to connect as `root`, using the authentication method provided by your VPS provider.

### 3. Configure variables

Check `inventory/group_vars/all.yml` and update the variables for your environment, including the path to your SSH public key.

### 4. Install Ansible dependencies

Make sure Ansible is installed locally, then run:

```bash
ansible-galaxy collection install -r requirements.yml
```

### 5. Run the bootstrap playbook

```bash
ansible-playbook playbooks/bootstrap.yml --ask-pass
```

This connects using the initial root password and configures the `deploy` user.

### 6. Switch to the deploy user

Update `inventory/hosts.ini` to use:

```ini
ansible_user=deploy
ansible_ssh_private_key_file=~/.ssh/{KEY_NAME}
ansible_ssh_common_args='-o IdentitiesOnly=yes'
```

Verify that Ansible can connect:

```bash
ansible production -m ping
```

You should receive `pong`.

Verify that privilege escalation works:

```bash
ansible production -b -m command -a "whoami"
```

The output should be `root`, because `-b` tells Ansible to execute the command using `sudo`.

## Set up the VPS

Once bootstrap is complete, run the main playbook to configure and secure the VPS.

### 1. Apply the configuration

**Warning:** SSH hardening disables password authentication and direct root SSH login. Make sure SSH key authentication works before continuing.

Keep an existing SSH session open while applying security changes.

```bash
ansible-playbook playbooks/site.yml
```

The security role configures:

- **UFW firewall:** Blocks incoming connections by default, except SSH (22), HTTP (80), and HTTPS (443).
- **SSH hardening:** Disables root SSH login and password authentication while keeping public-key authentication enabled.
- **Security packages:** Installs UFW and unattended-upgrades.

The SSH hardening configuration is stored in `/etc/ssh/sshd_config.d/00-hardening.conf`. Its filename ensures it takes precedence over later configuration snippets, such as those created by cloud-init.

### 2. Verify the configuration

On the VPS, check the firewall:

```bash
sudo ufw status verbose
```

It should report `Status: active`.

Validate the SSH configuration:

```bash
sudo sshd -t
```

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

### 3. Re-running the playbook

Ansible is idempotent: running the same playbook again should not make unnecessary changes.

To preview changes without applying them:

```bash
ansible-playbook playbooks/site.yml --check --diff
```

To apply changes:

```bash
ansible-playbook playbooks/site.yml
```

**Note:** Docker containers with publicly published ports can bypass normal UFW filtering. Internal services should not be exposed directly to the internet.

## VPN access

Some applications hosted on the VPS are not publicly accessible. They are expected to be accessed through a VPN that is automatically installed by the site playbook.
There is some manual setup to do to connect your local computer to tailscale:

### Authenticate on the VPS

On the VPS, run

```bash
sudo tailscale up
```

Tailscale will provide an authentication URL. Open it in your browser and sign in.

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
