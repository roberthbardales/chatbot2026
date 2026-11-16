# Despliegue En VPS

Estos pasos asumen un VPS con Ubuntu 22.04 o 24.04.

## 1. Instalar Dependencias

```bash
sudo apt update
sudo apt install -y python3 python3-venv python3-pip postgresql nginx git
```

## 2. Subir El Proyecto

Desde el equipo local:

```powershell
git add .
git commit -m "Preparar despliegue VPS"
git push
```

En el VPS:

```bash
cd /var/www
sudo git clone URL_DE_TU_REPOSITORIO chatbot2026
sudo chown -R $USER:$USER /var/www/chatbot2026
cd /var/www/chatbot2026
```

Si no se utiliza Git, subir el proyecto con WinSCP.

## 3. Crear Entorno Python

```bash
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
pip install gunicorn
```

## 4. Configurar `.env`

```bash
nano .env
```

Configurar como mínimo:

```env
SECRET_KEY=clave-secreta-larga
DEBUG=False
ALLOWED_HOSTS=tu-dominio.com,www.tu-dominio.com

DB_NAME=chatbot2026
DB_USER=chatbot
DB_PASSWORD=clave-segura
DB_HOST=127.0.0.1
DB_PORT=5432

OPENROUTER_API_KEY=tu-clave
OPENROUTER_MODEL=openai/gpt-4o-mini
SYSTEM_PROMPT=tu-prompt-de-GiPiX

GOOGLE_SHEET_PRODUCTS_CSV_URL=https://...

WHATSAPP_VERIFY_TOKEN=tu-token-de-verificacion
WHATSAPP_ACCESS_TOKEN=tu-token-de-meta
WHATSAPP_APP_SECRET=tu-secreto-de-meta
WHATSAPP_PHONE_NUMBER_ID=1348042578386824
WHATSAPP_API_VERSION=v26.0
```

No subir `.env` al repositorio.

## 5. Crear Base De Datos

```bash
sudo -u postgres psql
```

```sql
CREATE USER chatbot WITH PASSWORD 'clave-segura';
CREATE DATABASE chatbot2026 OWNER chatbot;
\q
```

## 6. Migrar Y Sincronizar

```bash
source venv/bin/activate
python manage.py migrate
python manage.py collectstatic --noinput
python manage.py sync_inventory
python manage.py check
```

## 7. Crear Servicio Gunicorn

```bash
sudo nano /etc/systemd/system/chatbot2026.service
```

```ini
[Unit]
Description=Chatbot2026 Django
After=network.target postgresql.service

[Service]
User=TU_USUARIO
WorkingDirectory=/var/www/chatbot2026
ExecStart=/var/www/chatbot2026/venv/bin/gunicorn chatbot2026.wsgi:application --bind 127.0.0.1:8000
Restart=always

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl daemon-reload
sudo systemctl enable chatbot2026
sudo systemctl start chatbot2026
sudo systemctl status chatbot2026
```

## 8. Configurar Nginx

```bash
sudo nano /etc/nginx/sites-available/chatbot2026
```

```nginx
server {
    listen 80;
    server_name tu-dominio.com www.tu-dominio.com;

    location /static/ {
        alias /var/www/chatbot2026/staticfiles/;
    }

    location / {
        proxy_pass http://127.0.0.1:8000;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }
}
```

```bash
sudo ln -s /etc/nginx/sites-available/chatbot2026 /etc/nginx/sites-enabled/
sudo nginx -t
sudo systemctl reload nginx
```

## 9. Activar HTTPS

```bash
sudo apt install -y certbot python3-certbot-nginx
sudo certbot --nginx -d tu-dominio.com -d www.tu-dominio.com
```

## 10. Configurar Meta

Callback URL:

```text
https://tu-dominio.com/whatsapp/webhook/
```

Suscribir el campo `messages` en Meta Developers.

El flujo actual procesa WhatsApp directamente desde el webhook. No es necesario ejecutar `whatsapp_worker`.

## 11. Revisar Errores

```bash
sudo journalctl -u chatbot2026 -f
sudo tail -f /var/log/nginx/error.log
```

## Estado Actual Del VPS

El despliegue de producción está operativo con esta configuración:

- Proyecto: `/var/www/chatbot2026`
- Sistema: Ubuntu
- Python: `3.10.12`
- Entorno virtual: `/var/www/chatbot2026/venv`
- Django: `6.1.3`
- Gunicorn: `26.2.0`
- PostgreSQL: `12`
- Base de datos: `db_chatbot2026`
- Extensión PostgreSQL `vector`: habilitada
- Gunicorn: `127.0.0.1:8006`
- Módulo WSGI: `chatbot2026.wsgi:application`
- Servicio systemd: `chatbot2026.service`
- Dominio: `chat-gipix.duckdns.org`
- HTTPS: configurado y operativo
- Static files: `/var/www/chatbot2026/staticfiles`

Comprobaciones realizadas:

```text
systemctl is-enabled chatbot2026 -> enabled
systemctl is-active chatbot2026  -> active
curl -I https://chat-gipix.duckdns.org -> HTTP/1.1 200 OK
python manage.py check -> System check identified no issues
```

Arquitectura:

```text
Internet/HTTPS
    -> Nginx
    -> Gunicorn 127.0.0.1:8006
    -> Django chatbot2026
    -> PostgreSQL db_chatbot2026
```

No modificar puertos, Nginx, systemd, PostgreSQL, dominio, SSL ni la arquitectura del despliegue salvo que sea estrictamente necesario.

## Actualización De Producción

```bash
cd /var/www/chatbot2026
source venv/bin/activate
git pull
pip install -r requirements.txt
python manage.py migrate
python manage.py collectstatic --noinput
python manage.py check
systemctl restart chatbot2026
```

No sobrescribir `.env`, credenciales ni la base de datos durante una actualización.
