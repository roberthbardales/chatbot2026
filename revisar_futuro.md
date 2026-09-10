# Revisión del proyecto

## Hallazgos

1. **Alto: Django fuera de soporte**
   - `requirements.txt:1` usa Django `3.2.25`, cuyo soporte terminó en 2024.
   - Riesgo de vulnerabilidades y problemas de compatibilidad.
   - Migrar como mínimo a Django 4.2 LTS o una versión soportada.

2. **Medio: `create_superuser()` no valida permisos**
   - `applications/users/managers.py:18-22` establece valores por defecto, pero permite sobrescribir `is_staff=False` o `is_superuser=False`.
   - Debe lanzar `ValueError` si esos flags no quedan correctamente configurados.

3. **Medio: posible configuración insegura de proxy HTTPS**
   - `chatbot2026/settings.py:172` confía en `X-Forwarded-Proto`.
   - Solo es seguro si el proxy elimina y reconstruye ese encabezado. Debe documentarse o configurarse según el proveedor de despliegue.

4. **Medio: protección de producción incompleta**
   - Faltan configuraciones explícitas como `CSRF_TRUSTED_ORIGINS`, `SECURE_CROSS_ORIGIN_OPENER_POLICY` y límites de hosts/orígenes según el despliegue.
   - `ALLOWED_HOSTS` depende completamente del `.env`; si queda vacío, la aplicación no funcionará correctamente en producción.

5. **Bajo: pruebas insuficientes**
   - `applications/home/tests.py` no contiene pruebas reales.
   - No hay pruebas para usuarios inactivos, permisos del panel admin, creación de superusuarios, fechas y teléfonos inválidos, acceso POST/CSRF a operaciones sensibles ni configuración de producción.

6. **Bajo: documentación desactualizada**
   - `base.md:162` afirma que hay 13 tests, pero `applications/users/tests.py` contiene más casos y la documentación no refleja exactamente el estado actual.
   - También describe el logout como comportamiento general sin destacar que ahora requiere POST.

## Verificación

No se modificó código durante la revisión. El chequeo no pudo ejecutarse porque el comando usó el Python global y no encontró Django:

```text
ModuleNotFoundError: No module named 'django'
```

El proyecto contiene un `venv`; la verificación debe ejecutarse usando `venv\Scripts\python.exe`, junto con una base PostgreSQL disponible.
