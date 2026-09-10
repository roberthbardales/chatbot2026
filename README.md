# chatbot2026

Aplicación Django para gestión de usuarios y autenticación.

## Requisitos

- Python 3.10+
- PostgreSQL

## Instalación

```powershell
python -m venv venv
.\venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

Crea un archivo `.env` basándote en las variables necesarias del proyecto:

```text
SECRET_KEY=una-clave-secreta-larga
DEBUG=False
ALLOWED_HOSTS=localhost,127.0.0.1
DB_NAME=db_chatbot2026
DB_USER=usuario
DB_PASSWORD=contraseña
DB_HOST=localhost
DB_PORT=5432
```

## Ejecución

```powershell
python manage.py migrate
python manage.py runserver
```

## Pruebas

```powershell
python manage.py test
```

El registro público crea usuarios normales. Las cuentas administrativas deben crearse mediante un proceso controlado, por ejemplo `createsuperuser` o el panel de administración.
