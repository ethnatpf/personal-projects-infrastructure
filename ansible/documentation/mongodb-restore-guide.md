# MongoDB restore guide

This guide covers the Ansible restore playbook (`playbooks/restore-mongodb.yml`) and PBM backups.

## How the restore works

1. By default, the playbook uses PBM's current backup catalog. Set `restore_sync_storage=true` to synchronize the selected storage catalog before listing backups.
2. It selects the newest completed logical backup (`latest`) or the explicitly named backup from the requested source.
3. It stops the scheduled PBM backup and cleanup timers.
4. **By default, it creates a fresh safety backup in the `pre_restore` storage profile** before overwriting MongoDB data. Safety backups are stored separately from hourly and daily backups and are not subject to their automatic cleanup schedules.
5. It stops the Immersion Tracker backend before the safety backup and restore. After an explicit `RESTORE` confirmation, it restores the selected backup, checks MongoDB, and resumes the timers. A successful restore starts the backend again; if the restore or MongoDB validation fails, the backend stays stopped.

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
2. Synchronize PBM's storage catalog because the clean VPS may not yet have the remote backup list. Enable this with `restore_sync_storage=true`.
3. Ensure no application is writing to MongoDB. When safety backup skipping is requested, the playbook automatically checks that the target database is empty and fails if it contains data.
4. Run the restore with storage synchronization enabled and the safety backup skipped:

```bash
ansible-playbook playbooks/restore-mongodb.yml --ask-vault-pass \
  -e restore_profile=daily \
  -e restore_sync_storage=true \
  -e restore_skip_safety_backup=true
```

Change `restore_profile=daily` to `main` for hourly backups or `pre_restore` for safety backups. Add `-e restore_backup_name=<BACKUP_NAME>` to choose a specific snapshot.

## After restoring

Confirm the MongoDB health check passes, the scheduled PBM timers are running, and application data is correct before allowing application writes again. Check PBM status and logs if anything fails. If the restore failed, the backend remains stopped; restart it only after MongoDB is healthy and ready for application writes.
