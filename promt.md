# Prompt de estructura y metodología

Quiero que desarrolles este proyecto respetando la estructura y metodología del proyecto de referencia.

Reglas obligatorias:

1. Inspecciona primero toda la estructura existente antes de modificar archivos.
2. Respeta las convenciones actuales; no reemplaces configuraciones sin necesidad.
3. Separa responsabilidades:
   - models: datos
   - views/controllers: flujo HTTP
   - services: lógica de negocio
   - urls: rutas
   - templates/components: interfaz
   - integrations/providers: APIs externas
4. Reutiliza configuraciones, layouts, autenticación, base de datos y estilos existentes.
5. Usa variables de entorno para secretos y agrega las variables necesarias al `.env.example`.
6. No dupliques funcionalidades ni instales dependencias innecesarias.
7. Mantén los cambios pequeños, claros e incrementales.
8. Agrega o actualiza tests para toda funcionalidad importante.
9. Después de cada bloque ejecuta las comprobaciones disponibles:
   - validación del proyecto
   - migraciones
   - tests
   - lint o build, si existe
10. Actualiza la documentación del proyecto cuando cambien la arquitectura, comandos,
    variables de entorno, rutas o reglas de negocio.
11. No elimines archivos ni cambies datos existentes sin avisar primero.
12. Antes de implementar, entrega un resumen corto de:
    - qué encontraste;
    - qué reutilizarás;
    - qué crearás;
    - cuál será el plan.
13. Al finalizar informa:
    - archivos creados;
    - archivos modificados;
    - comandos ejecutados;
    - resultado de las pruebas;
    - variables de entorno necesarias;
    - problemas pendientes.

Adapta esta metodología al stack real del proyecto. No copies nombres específicos del proyecto
de referencia como `chatbot2026`, `applications.chat` o `GEMINI_API_KEY` si no corresponden.
