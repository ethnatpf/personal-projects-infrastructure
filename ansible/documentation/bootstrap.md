# Bootstrap a VPS

Follow these steps to bootstrap a fresh VPS running Ubuntu.

The bootstrap playbook creates a `deploy` user, configures SSH key authentication, and grants administrative privileges through `sudo`.

## Before you begin: update the VPS

Before doing anything else, SSH into the new VPS using the access details from your VPS provider:

```bash
ssh root@{VPS_IP}
```

On Ubuntu, update the package lists, install all available upgrades, and reboot:

```bash
sudo apt update
sudo apt full-upgrade
sudo reboot
```

Reconnect to the VPS after it reboots, then continue with the bootstrap steps below.

## 1. Generate an SSH key

On your local machine, generate a new SSH key:

```bash
ssh-keygen -t ed25519 -f ~/.ssh/{KEY_NAME} -C "{KEY_NAME}"
```

Update `ssh_public_key_path` in `inventory/group_vars/all/all.yml` to point to the generated public key (`~/.ssh/{KEY_NAME}.pub`).

If your SSH key has a passphrase, load it into an SSH agent:

```bash
eval "$(ssh-agent -s)"
ssh-add ~/.ssh/{KEY_NAME}
```

Ansible cannot interactively prompt for an SSH key passphrase in the usual way. The SSH agent allows Ansible to use your unlocked key without removing its passphrase.

## 2. Configure the inventory

Copy `inventory/hosts.ini.example` to `inventory/hosts.ini` and fill in your VPS information.

Initially, configure Ansible to connect as the root user, using the authentication method provided by your VPS provider.

## 3. Configure variables

Check `inventory/group_vars/all/all.yml` and update the variables for your environment, including `ssh_public_key_path` if your SSH public key is stored elsewhere.

## 4. Install Ansible dependencies

Make sure Ansible is installed locally, then run:

```bash
ansible-galaxy collection install -r requirements.yml
```

## 5. Run the bootstrap playbook

```bash
ansible-playbook playbooks/bootstrap.yml --ask-pass
```

This connects using the initial root password and configures the `deploy` user.

## 6. Switch to the deploy user

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
