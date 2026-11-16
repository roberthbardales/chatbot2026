"""Full pg_dump/pg_restore rehearsal, restricted to Django's isolated test DB.

Never invokes workers, providers, inventory downloads or restores to the source.
The disposable target is generated here and removed only if this test created it.
"""
import hashlib
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import uuid
from datetime import timedelta
from decimal import Decimal

import psycopg2
from psycopg2 import sql
from django.contrib.sessions.backends.db import SessionStore
from django.contrib.sessions.models import Session
from django.db import connection
from django.test import TransactionTestCase
from django.utils import timezone

from applications.users.models import User
from applications.whatsapp.models import (DeliveryEvent, Outbox, OutboxRetryAudit,
                                           WhatsAppContact, WhatsAppMessage, WorkerHeartbeat)
from .models import (AIUsageDay, Conversation, InventorySyncLock, InventorySyncRun,
                     Message, Product, UsageBucket, UsageLease)


class PostgreSQLBackupRestoreTests(TransactionTestCase):
    def test_full_backup_restores_schema_data_sequences_and_constraints_in_disposable_database(self):
        self.assertEqual(connection.vendor, 'postgresql')
        source_name = str(connection.settings_dict['NAME'])
        self.assertTrue(source_name.startswith('test_'), 'Backup rehearsal requires a test_ source database.')
        self.assertNotEqual(source_name, os.environ.get('DB_NAME'), 'Refusing to dump the habitual database.')
        self.assertTrue(connection.get_autocommit(), 'CREATE DATABASE must not run inside an atomic transaction.')
        dump = os.environ.get('TEST_PG_DUMP') or shutil.which('pg_dump')
        restore = os.environ.get('TEST_PG_RESTORE') or shutil.which('pg_restore')
        if not dump or not restore:
            self.skipTest('pg_dump/pg_restore unavailable; full backup restoration was NOT verified.')
        server_major = connection.pg_version // 10000
        for program in [dump, restore]:
            result = subprocess.run([program, '--version'], capture_output=True, text=True, check=True, timeout=15)
            version = re.search(r'(\d+)\.', result.stdout)
            if version is None or int(version.group(1)) != server_major:
                self.skipTest('PostgreSQL client/server major versions differ; full restoration was NOT verified.')

        now = timezone.now()
        user = User.objects.create_user('restore-fixture@example.test', 'Fixture-only-password', occupation=User.EMPLEADO)
        product = Product.objects.create(sku='RESTORE-P', name='Producto de ensayo', price=Decimal('0.10'), stock=3)
        conversation = Conversation.objects.create(user=user)
        message = Message.objects.create(conversation=conversation, role=Message.ASSISTANT, content='Respuesta de ensayo',
                                          inventory_context={'selected_sku': product.sku})
        session = Session.objects.create(session_key=uuid.uuid4().hex,
                                          session_data=SessionStore().encode({'fixture': str(conversation.pk)}),
                                          expire_date=now + timedelta(days=1))
        run = InventorySyncRun.objects.create(status='applied', finished_at=now, summary={'price': '0.10'})
        InventorySyncLock.objects.create(pk=1)
        AIUsageDay.objects.create(day=now.date(), requests=3, reserved_tokens=1234)
        UsageBucket.objects.create(key='rate:restore-fixture', window_started_at=now, count=2)
        UsageLease.objects.create(scope='fixture-expired', expires_at=now - timedelta(seconds=1))
        contact = WhatsAppContact.objects.create(phone_number='51900001111', mode=WhatsAppContact.HUMAN)
        WhatsAppMessage.objects.create(contact=contact, message_id='restore-incoming', text='Consulta ensayo', status='pending')
        assistant = WhatsAppMessage.objects.create(contact=contact, message_id='restore-assistant', text='Respuesta ensayo',
                                                    is_from_customer=False, status='processed', inventory_context={'selected_sku': product.sku})
        accepted = Outbox.objects.create(contact=contact, message=assistant, content='Aceptado ensayo', sent=True,
                                         meta_message_id='wamid.restore-fixture', delivery_status='read', read_at=now, sent_at=now)
        pending = Outbox.objects.create(contact=contact, message=assistant, content='Pendiente ensayo')
        failed = Outbox.objects.create(contact=contact, message=assistant, content='Fallo ensayo', failed_at=now, attempts=5)
        event = DeliveryEvent.objects.create(fingerprint=hashlib.sha256(b'restore-fixture').hexdigest(),
                                             meta_message_id=accepted.meta_message_id, recipient_id=contact.phone_number,
                                             phone_number_id='fixture-account', status='read', occurred_at=now,
                                             outbox=accepted, applied_at=now)
        audit = OutboxRetryAudit.objects.create(actor=user, actor_identifier=str(user.pk), outbox=failed,
                                                group_id=failed.group_id, reason='Ensayo de restauración',
                                                snapshot=[{'outbox_id': failed.pk, 'attempts': 5}])
        WorkerHeartbeat.objects.create(heartbeat_at=now, last_progress_at=now, stopped_at=now, phase='stopped')
        with connection.cursor() as cursor:
            cursor.execute('SELECT current_database()')
            self.assertEqual(cursor.fetchone()[0], source_name)
            cursor.execute('SELECT count(*) FROM django_migrations')
            migration_count = cursor.fetchone()[0]

        params = connection.get_connection_params()
        environment = os.environ.copy()
        mapping = {'host': 'PGHOST', 'port': 'PGPORT', 'user': 'PGUSER', 'password': 'PGPASSWORD',
                   'sslmode': 'PGSSLMODE', 'sslrootcert': 'PGSSLROOTCERT', 'sslcert': 'PGSSLCERT',
                   'sslkey': 'PGSSLKEY', 'options': 'PGOPTIONS', 'service': 'PGSERVICE'}
        for parameter, variable in mapping.items():
            if params.get(parameter) is not None:
                environment[variable] = str(params[parameter])
        target_name = 'test_rehearsal_' + uuid.uuid4().hex
        self.assertRegex(target_name, r'^test_rehearsal_[0-9a-f]{32}$')
        self.assertNotEqual(target_name, source_name)
        temp_root = Path(tempfile.gettempdir()) / 'opencode'
        temp_root.mkdir(exist_ok=True)
        created = False
        target_connection = None
        with tempfile.TemporaryDirectory(prefix='postgres-rehearsal-', dir=temp_root) as directory:
            archive = str(Path(directory) / 'isolated-test.dump')
            try:
                # Credentials are environment-only, never command-line arguments or test output.
                result = subprocess.run([dump, '--no-password', '--format=custom', '--no-owner', '--no-privileges',
                                         '--file', archive, '--dbname', source_name],
                                        env=environment, capture_output=True, text=True, timeout=120)
                self.assertEqual(result.returncode, 0, 'pg_dump failed on the isolated source; inspect local PostgreSQL configuration.')
                self.assertGreater(Path(archive).stat().st_size, 0)
                with connection.cursor() as cursor:
                    template = connection.settings_dict.get('TEST', {}).get('TEMPLATE') or 'template1'
                    cursor.execute(sql.SQL('CREATE DATABASE {} TEMPLATE {}').format(sql.Identifier(target_name), sql.Identifier(template)))
                created = True
                target_params = dict(params, dbname=target_name)
                target_connection = psycopg2.connect(**target_params)
                with target_connection.cursor() as cursor:
                    cursor.execute("SELECT tablename FROM pg_tables WHERE schemaname='public'")
                    self.assertEqual(cursor.fetchall(), [], 'The disposable target template must not contain application tables.')
                target_connection.commit()
                result = subprocess.run([restore, '--no-password', '--exit-on-error', '--no-owner', '--no-privileges',
                                         '--no-comments', '--dbname', target_name, archive],
                                        env=environment, capture_output=True, text=True, timeout=120)
                diagnostic = result.stderr[:4000]
                if params.get('password'):
                    diagnostic = diagnostic.replace(str(params['password']), '[redacted]')
                self.assertEqual(result.returncode, 0, 'pg_restore failed only in the disposable target: ' + diagnostic)
                with target_connection.cursor() as cursor:
                    cursor.execute('SELECT current_database()')
                    self.assertEqual(cursor.fetchone()[0], target_name)
                    cursor.execute('SELECT price, stock FROM chat_product WHERE id=%s', [product.pk])
                    self.assertEqual(cursor.fetchone(), (Decimal('0.10'), 3))
                    cursor.execute('SELECT inventory_context FROM chat_message WHERE id=%s', [message.pk])
                    self.assertEqual(cursor.fetchone()[0], {'selected_sku': 'RESTORE-P'})
                    cursor.execute('SELECT session_data FROM django_session WHERE session_key=%s', [session.session_key])
                    self.assertEqual(cursor.fetchone()[0], session.session_data)
                    cursor.execute('SELECT sent, delivery_status, meta_message_id FROM whatsapp_outbox WHERE id=%s', [accepted.pk])
                    self.assertEqual(cursor.fetchone(), (True, 'read', 'wamid.restore-fixture'))
                    cursor.execute('SELECT sent FROM whatsapp_outbox WHERE id=%s', [pending.pk])
                    self.assertFalse(cursor.fetchone()[0])
                    cursor.execute('SELECT outbox_id FROM whatsapp_deliveryevent WHERE id=%s', [event.pk])
                    self.assertEqual(cursor.fetchone()[0], accepted.pk)
                    cursor.execute('SELECT actor_id, snapshot FROM whatsapp_outboxretryaudit WHERE id=%s', [audit.pk])
                    self.assertEqual(cursor.fetchone(), (user.pk, [{'outbox_id': failed.pk, 'attempts': 5}]))
                    cursor.execute('SELECT status, summary FROM chat_inventorysyncrun WHERE id=%s', [str(run.pk)])
                    self.assertEqual(cursor.fetchone(), ('applied', {'price': '0.10'}))
                    cursor.execute('SELECT count(*) FROM django_migrations')
                    self.assertEqual(cursor.fetchone()[0], migration_count)
                    cursor.execute('''INSERT INTO chat_product
                        (sku,name,category,brand,model,specs,price,stock,unit,keywords,description,active,content_hash,created_at,updated_at)
                        VALUES ('RESTORE-NEW','Nuevo','','','','',0.20,1,'','','',true,'',CURRENT_TIMESTAMP,CURRENT_TIMESTAMP)
                        RETURNING id''')
                    self.assertGreater(cursor.fetchone()[0], product.pk)
                    with self.assertRaises(psycopg2.IntegrityError):
                        cursor.execute('UPDATE whatsapp_deliveryevent SET outbox_id=-1 WHERE id=%s', [event.pk])
                        target_connection.commit()
                target_connection.rollback()
                self.assertFalse(Outbox.objects.get(pk=pending.pk).sent)
            finally:
                if target_connection is not None:
                    target_connection.close()
                if created:
                    # Only the exact fresh database generated and created above may be dropped.
                    self.assertRegex(target_name, r'^test_rehearsal_[0-9a-f]{32}$')
                    self.assertNotEqual(target_name, source_name)
                    with connection.cursor() as cursor:
                        cursor.execute(sql.SQL('DROP DATABASE {}').format(sql.Identifier(target_name)))
