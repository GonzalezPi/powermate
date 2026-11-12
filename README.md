# PowerMate

Aplicación web en PHP para consultar el pronóstico meteorológico y predecir la
generación de energía de una instalación solar fotovoltaica con un modelo de
inteligencia artificial en Python.

La instalación está configurada para **Junín, Provincia de Buenos Aires**
(`-34.5833`, `-60.95`), con un sistema de 3 kWp, inclinación de 30° y azimut
sur.

## Funciones

- Descarga y guarda el pronóstico horario de Open-Meteo en MySQL.
- Predice energía diaria y potencia horaria a partir del clima almacenado.
- Guarda las predicciones y su nivel de confianza en la base de datos.
- Reentrena el modelo cuando se acumulan mediciones reales nuevas.
- Muestra un panel con el pronóstico, el estado del modelo y el historial de
  entrenamientos.

## Requisitos

- PHP 8.2 con `pdo_mysql` y `curl` (por ejemplo, XAMPP).
- MySQL o MariaDB.
- Python 3.10 o superior.

## Instalación

1. Crea la base de datos `sistema_monitoreo` en MySQL.

2. Configura la conexión:

   ```bash
   cp config/database.example.php config/database.php
   ```

   Edita `config/database.php` con tus credenciales. Ese archivo está excluido
   de Git.

3. Crea el entorno de Python e instala las dependencias:

   ```bash
   cd Model
   python3 -m venv .venv
   .venv/bin/pip install -r requirements.txt
   ```

4. Ejecuta la migración de la base de datos:

   ```bash
   /opt/lampp/bin/php config/migracion_ai.php
   ```

5. Ajusta la ubicación y el sistema fotovoltaico en `config/ai.php`. Para
   cambios locales sin modificar el archivo versionado, crea
   `config/ai.local.php`.

6. Abre la aplicación en el navegador:

   ```text
   http://localhost/powermate-main/
   ```

## Uso

- **Actualizar clima**: descarga el pronóstico y, opcionalmente, genera la
  predicción.
- **Predecir energía**: usa el clima ya guardado en MySQL.
- **Reentrenar modelo**: fuerza un nuevo entrenamiento con los datos
  disponibles.

También existe una tarea programada:

```bash
/opt/lampp/bin/php cron_actualizar.php
```

Ejemplo de cron cada hora:

```cron
0 * * * * /opt/lampp/bin/php /ruta/powermate-main/cron_actualizar.php >> /tmp/powermate.log 2>&1
```

## Documentación

- [`INTEGRACION_IA.md`](INTEGRACION_IA.md): arquitectura de la integración,
  migración, endpoints, aprendizaje continuo y diagnóstico.
- [`Model/README.md`](Model/README.md): documentación del modelo de Python.

## Pruebas

```bash
cd Model
env -u LD_LIBRARY_PATH -u LD_PRELOAD .venv/bin/python -m pytest -q
```
