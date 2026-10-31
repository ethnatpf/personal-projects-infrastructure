# MongoDB restore guide

This guide covers the Ansible restore playbook (`playbooks/restore-mongodb.yml`) and PBM backups.

## How the restore works

1. The playbook synchronizes the selected PBM storage catalog and waits until the backup list is available.
2. It selects the newest completed logical backup (`latest`) or the explicitly named backup from the requested source.
3. It stops the scheduled PBM backup and cleanup timers.
4. **By default, it creates a fresh safety backup in the `pre_restore` storage profile** before overwriting MongoDB data. Safety backups are stored separately from hourly and daily backups and are not subject to their automatic cleanup schedules.
5. It asks for an explicit `RESTORE` confirmation, restores the selected backup, checks MongoDB, and resumes the timers.

## Prerequisites

- Run commands from the Ansible project directory.
- MongoDB and the PBM agent must already be running and configured, with access to the relevant backup storage.
- Have the Ansible Vault password available.
- Ensure no application is writing to MongoDB during the restore.

## List available backups

On the VPS, from `/opt/mongodb-backup`:

```bash
docker compose exec -T pbm pbm list
```

Backups with no profile are the hourly backups, the profile that we call "main".

## Restore from hourly backups (default)

Restore the newest completed hourly backup:

```bash
ansible-playbook playbooks/restore-mongodb.yml --ask-vault-pass
```

Restore a specific hourly backup:

```bash
ansible-playbook playbooks/restore-mongodb.yml --ask-vault-pass \
  -e restore_profile=main \
  -e restore_backup_name=2026-10-31T15:10:12Z
```

## Restore from daily backups

Restore the newest completed daily backup:

```bash
ansible-playbook playbooks/restore-mongodb.yml --ask-vault-pass \
  -e restore_profile=daily
```

Restore a specific daily backup by adding `-e restore_backup_name=<BACKUP_NAME>`.

## Restore from a safety backup

Restore the newest completed pre restore backup:

```bash
ansible-playbook playbooks/restore-mongodb.yml --ask-vault-pass \
  -e restore_profile=pre_restore
```

For a specific safety backup, add `-e restore_backup_name=<BACKUP_NAME>`.

**Note:** By default, the playbook also creates _another_ pre-restore safety backup of the current database before restoring the chosen safety backup.

## Restore onto a clean VPS

1. Provision the VPS using the configure-vps playbook.
2. Ensure no application is writing to MongoDB. When safety backup skipping is requested, the playbook automatically checks that the target database is empty and fails if it contains data.
3. Run the restore with the safety backup skipped:

```bash
ansible-playbook playbooks/restore-mongodb.yml --ask-vault-pass \
  -e restore_profile=daily \
  -e restore_skip_safety_backup=true
```

Change `restore_profile=daily` to `main` for hourly backups or `pre_restore` for safety backups. Add `-e restore_backup_name=<BACKUP_NAME>` to choose a specific snapshot.

## After restoring

Confirm the MongoDB health check passes, the scheduled PBM timers are running, and application data is correct before allowing application writes again. Check PBM status and logs if anything fails. Do not assume a failed playbook automatically restarted the application.
