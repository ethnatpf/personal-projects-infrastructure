# MongoDB backups

This page describes the MongoDB backup setup deployed by the Ansible `mongodb_backup` role. Percona Backup for MongoDB (PBM) runs in Docker Compose and manages the backup catalog and logical backups. Systemd timers on the VPS start the scheduled PBM backup and cleanup commands.

## Storage profiles

The hourly storage is PBM's main configuration (`main` profile). Daily backups use the separate `daily` profile. Each storage uses its own S3-compatible bucket in a different region.

| Backup | PBM profile | Bucket                                     | Region      | Schedule             | Retention |
| ------ | ----------- | ------------------------------------------ | ----------- | -------------------- | --------- |
| Hourly | `main`      | `immersion-tracker-mongodb-hourly-backups` | `ca-east-1` | Hourly, at minute 10 | 7 days    |
| Daily  | `daily`     | `immersion-tracker-mongodb-daily-backups`  | `us-east-1` | Daily at 02:30       | 90 days   |

Both scheduled jobs create logical backups.

The restore playbook also registers a `pre_restore` storage profile in a separate bucket in `ca-east-1`. It uses this profile for a safety backup before a restore. This profile is not part of the hourly and daily schedule and is not covered by their automatic cleanup jobs. See the [MongoDB restore guide](mongodb-restore-guide.md) for restore procedures and options.

## Useful commands

Run these on the VPS from `/opt/mongodb-backup`:

```bash
docker compose exec -T pbm pbm status
docker compose exec -T pbm pbm list
```

Backups without a named profile are hourly (`main`) backups. To sync the daily backup catalog from its remote storage:

```bash
docker compose exec -T pbm pbm profile sync daily --wait
```

## Troubleshooting

### Grafana alerts on a newly set up VPS

Grafana may alert after a VPS is first set up because the scheduled PBM backup and cleanup jobs have not run yet. These alerts should resolve on their own within 24 hours. To clear them sooner and verify that the jobs work, start them manually on the VPS:

```bash
systemctl start pbm-cleanup_daily.service
systemctl start pbm-cleanup_hourly.service
systemctl start pbm-daily.service
```

The hourly backup job is not included inside the above command as it should run by itself every hour, compared to the other ones that run every day.

### PBM reports that it cannot get the storage configuration

If PBM logs an error like:

```text
[agentCheckup] check storage connection: unable to get storage: get config: get: mongo: no documents in result
```

PBM may not have loaded its main storage configuration into MongoDB. The `mongodb_backup` role loads it with the `Configure PBM main storage` handler. The handler runs this command as root inside the PBM container because the rendered configuration file is root-owned and restricted:

```bash
cd /opt/mongodb-backup
docker compose exec -T --user 0 pbm pbm config --file /etc/pbm/storage-hourly.yml
```

If the others configurations also didn't load, load them with

```bash
docker compose exec -T --user 0 pbm pbm profile add --wait daily /etc/pbm/storage-daily.yml
docker compose exec -T --user 0 pbm pbm profile add --wait pre_restore /etc/pbm/storage-pre-restore.yml
```
