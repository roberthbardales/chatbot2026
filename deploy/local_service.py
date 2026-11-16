"""Local-only service with private logs. Worker sends real queues; no inventory sync."""
import argparse
from datetime import datetime, timezone
import os
from pathlib import Path
import sys
import traceback

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('role', choices=['web', 'worker', 'health'])
    parser.add_argument('--log-dir', required=True)
    args = parser.parse_args()
    directory = Path(args.log_dir).resolve()
    if not directory.is_dir():
        raise SystemExit('Create a private log directory first.')
    with (directory / (args.role + '.log')).open('a', encoding='utf-8', buffering=1) as log:
        stdout, stderr = sys.stdout, sys.stderr
        try:
            sys.stdout = log
            sys.stderr = log
            print(f'[{datetime.now(timezone.utc).isoformat()}] Starting local role: {args.role}', flush=True)
            os.chdir(ROOT)
            os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'chatbot2026.settings')
            from django.core.management import execute_from_command_line
            command = {'web': ['runserver', '127.0.0.1:8000', '--noreload'],
                       'worker': ['whatsapp_worker'], 'health': ['whatsapp_healthcheck']}[args.role]
            execute_from_command_line(['manage.py'] + command)
        except Exception:
            traceback.print_exc(file=log)
            raise
        finally:
            sys.stdout, sys.stderr = stdout, stderr


if __name__ == '__main__':
    main()
