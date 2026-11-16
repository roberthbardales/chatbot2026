"""Create a new private PostgreSQL archive before migrating; never restores data."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
from datetime import datetime, timezone

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'chatbot2026.settings')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output-dir', required=True)
    args = parser.parse_args()
    output = Path(args.output_dir).resolve()
    if not output.is_dir():
        raise SystemExit('Create a private output directory first.')
    if output == ROOT or ROOT in output.parents:
        raise SystemExit('Keep database backups outside the Git repository.')
    archive = output / 'before-migrations.dump'
    manifest = output / 'backup.json'
    if archive.exists() or manifest.exists():
        raise SystemExit('Refusing to overwrite an existing backup.')
    import django
    django.setup()
    from django.db import connection
    dump, restore = shutil.which('pg_dump'), shutil.which('pg_restore')
    if not dump or not restore:
        raise SystemExit('PostgreSQL backup tools are unavailable.')
    params = connection.get_connection_params()
    environment = os.environ.copy()
    for key, variable in {'host': 'PGHOST', 'port': 'PGPORT', 'user': 'PGUSER', 'password': 'PGPASSWORD',
                          'sslmode': 'PGSSLMODE', 'sslrootcert': 'PGSSLROOTCERT', 'sslcert': 'PGSSLCERT',
                          'sslkey': 'PGSSLKEY', 'options': 'PGOPTIONS', 'service': 'PGSERVICE'}.items():
        if params.get(key) is not None:
            environment[variable] = str(params[key])
    with connection.cursor() as cursor:
        cursor.execute('SELECT app, name FROM django_migrations ORDER BY app, name')
        migrations = [list(row) for row in cursor.fetchall()]
    with archive.open('xb') as stream:
        result = subprocess.run([dump, '--no-password', '--format=custom', '--dbname', str(params['dbname'])],
                                env=environment, stdout=stream, stderr=subprocess.PIPE, timeout=180)
    if result.returncode:
        raise SystemExit('pg_dump failed; migrations must not proceed. No credentials are printed.')
    checked = subprocess.run([restore, '--list', str(archive)], capture_output=True, timeout=30)
    if checked.returncode or archive.stat().st_size == 0:
        raise SystemExit('Backup archive validation failed; migrations must not proceed.')
    digest = hashlib.sha256()
    with archive.open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(chunk)
    receipt = {'created_at_utc': datetime.now(timezone.utc).isoformat(), 'archive': archive.name,
               'bytes': archive.stat().st_size, 'sha256': digest.hexdigest(),
               'postgres_major': connection.pg_version // 10000, 'applied_migrations': migrations,
               'archive_catalog_verified': True, 'restored': False}
    with manifest.open('x', encoding='utf-8') as stream:
        json.dump(receipt, stream, ensure_ascii=False, indent=2)
    print(json.dumps({'backup': str(archive), 'manifest': str(manifest), 'bytes': receipt['bytes'],
                      'sha256': receipt['sha256'], 'archive_catalog_verified': True}))


if __name__ == '__main__':
    main()
