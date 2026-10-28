# Bootstrap

Follow these steps to bootstrap a fresh VPS
This assumes the VPS is on ubuntu or debian.

1. Generate an SSH key

```bash
ssh-keygen -t ed25519 -f ~/.ssh/{KEY_NAME} -C "{KEY_NAME}"
```

If you choose to use a passphrase, also run

```bash
eval "$(ssh-agent -s)"
```

```bash
ssh-add ~/.ssh/{PRIVATE_KEY_PATH}
```

Ansible doesn't automatically ask for a passphrase - which will cause an error. This securely keeps the key unlocked so ansible can use it.

2. Create ini file
   Duplicate the `inventory/hosts.ini.example` file and call it `hosts.ini`. Fill the values inside it.

3. Variables
   Make sure all the variables inside `inventory/group_vars/all.yml` are accurate.

4. Install ansible dependencies

```bash
ansible-galaxy collection install -r requirements.yml
```

5. Run the bootstrap playbook

```bash
ansible-playbook playbooks/bootstrap.yml --ask-pass
```

6. Update ansible user

Inside inventory/hosts.ini, update the ansible user to deploy instead of root.
