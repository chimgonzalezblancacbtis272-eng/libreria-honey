# Librería Honey — nuevas funciones

Se conserva la aplicación Flask/Python, la base `LIBRERIA_HONEY`, la colección `libros`, MongoDB local como alternativa y MongoDB Atlas mediante `MONGO_URI`. Los PDFs y portadas siguen en GridFS.

## Funciones añadidas
- Registro e inicio/cierre de sesión; contraseñas almacenadas con hash.
- Merch con búsqueda, imagen, precio, stock, carrito y eliminación individual de productos del carrito.
- Dirección de entrega solicitada en el paso de compra.
- Pantalla de tarjeta solo como **demostración escolar**: no conecta con una pasarela ni cobra y no guarda datos de tarjeta. No introducir datos reales.
- Panel Admin para añadir libros digitales (PDF y portada por separado), editar/eliminar libros, crear/editar/eliminar mercancía y configurar hasta cuatro ofertas/recomendaciones con imagen.
- La lectura de los PDFs continúa siendo gratuita.

## Configuración local
1. Instala dependencias: `pip install -r requirements.txt`.
2. Configura `MONGO_URI` si usarás MongoDB Atlas. Si no lo configuras, la aplicación mantiene su alternativa local `mongodb://127.0.0.1:27017/`.
3. Define las variables de entorno `SECRET_KEY`, `ADMIN_EMAIL` y `ADMIN_PASSWORD` antes de ejecutar la aplicación. Usa una contraseña administrativa fuerte y no publiques estas variables en GitHub. El primer inicio con el correo y contraseña configurados crea/activa la cuenta administradora.
4. Ejecuta `python app.py`.

Ejemplo PowerShell (solo para la terminal actual):
```powershell
$env:SECRET_KEY = "cambia-esto-por-una-clave-larga-y-aleatoria"
$env:ADMIN_EMAIL = "tu-correo-admin@example.com"
$env:ADMIN_PASSWORD = "usa-una-contrasena-fuerte"
python app.py
```

**Importante:** el checkout es una simulación y no procesa pagos. Para vender de verdad, integra un proveedor de pagos compatible y evita recibir o almacenar números de tarjeta/CVV en tu servidor.
