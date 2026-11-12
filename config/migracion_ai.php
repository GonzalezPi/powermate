<?php

/**
 * Migración de la base de datos para la integración de la IA.
 *
 * Se puede ejecutar desde la terminal:
 *
 *     php config/migracion_ai.php
 *
 * o abrirla en el navegador. Es idempotente: se puede repetir sin riesgo.
 */

declare(strict_types=1);

require_once __DIR__ . "/database.php";

$esCLI = PHP_SAPI === "cli";

function mostrar(string $mensaje): void
{
    global $esCLI;
    if ($esCLI) {
        echo $mensaje . PHP_EOL;
        return;
    }
    echo "<p>" . htmlspecialchars($mensaje, ENT_QUOTES, "UTF-8") . "</p>";
}

function tablaExiste(PDO $conexion, string $tabla): bool
{
    $sql = "SELECT COUNT(*) FROM information_schema.TABLES
            WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = :tabla";
    $consulta = $conexion->prepare($sql);
    $consulta->execute([":tabla" => $tabla]);
    return (int) $consulta->fetchColumn() > 0;
}

function columnaExiste(PDO $conexion, string $tabla, string $columna): bool
{
    $sql = "SELECT COUNT(*) FROM information_schema.COLUMNS
            WHERE TABLE_SCHEMA = DATABASE()
              AND TABLE_NAME = :tabla
              AND COLUMN_NAME = :columna";
    $consulta = $conexion->prepare($sql);
    $consulta->execute([":tabla" => $tabla, ":columna" => $columna]);
    return (int) $consulta->fetchColumn() > 0;
}

function indiceExiste(PDO $conexion, string $tabla, string $indice): bool
{
    $sql = "SELECT COUNT(*) FROM information_schema.STATISTICS
            WHERE TABLE_SCHEMA = DATABASE()
              AND TABLE_NAME = :tabla
              AND INDEX_NAME = :indice";
    $consulta = $conexion->prepare($sql);
    $consulta->execute([":tabla" => $tabla, ":indice" => $indice]);
    return (int) $consulta->fetchColumn() > 0;
}

if (!$esCLI) {
    echo "<!DOCTYPE html><html lang='es'><head><meta charset='utf-8'>"
        . "<title>Migración IA · PowerMate</title></head><body>";
    echo "<h1>Migración de la base de datos</h1>";
}

try {
    // ------------------------------------------------------------------
    // 1. Ampliar la tabla de clima con las variables que usa el modelo.
    // ------------------------------------------------------------------
    $columnasClima = [
        "Humedad_relativa" => "DECIMAL(5,2) NULL DEFAULT NULL",
        "Nubosidad" => "DECIMAL(5,2) NULL DEFAULT NULL",
        "Viento" => "DECIMAL(6,2) NULL DEFAULT NULL",
        "Radiacion_global" => "DECIMAL(7,2) NULL DEFAULT NULL",
        "Radiacion_directa" => "DECIMAL(7,2) NULL DEFAULT NULL",
        "Radiacion_difusa" => "DECIMAL(7,2) NULL DEFAULT NULL",
        "Radiacion_inclinada" => "DECIMAL(7,2) NULL DEFAULT NULL",
        "Nieve" => "DECIMAL(6,3) NULL DEFAULT NULL",
        "Probabilidad_precipitacion" => "DECIMAL(5,2) NULL DEFAULT NULL",
        "Codigo_tiempo" => "INT NULL DEFAULT NULL",
        "Es_dia" => "TINYINT(1) NULL DEFAULT NULL",
        "Origen" => "VARCHAR(20) NOT NULL DEFAULT 'forecast'",
        "Actualizado_en" => "DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
                             ON UPDATE CURRENT_TIMESTAMP",
    ];

    foreach ($columnasClima as $columna => $definicion) {
        if (!columnaExiste($conexion, "Predicciones_Meteorologicas", $columna)) {
            $conexion->exec(
                "ALTER TABLE Predicciones_Meteorologicas
                 ADD COLUMN `$columna` $definicion"
            );
            mostrar("Columna añadida: Predicciones_Meteorologicas.$columna");
        }
    }

    // ------------------------------------------------------------------
    // 2. Evitar duplicados por fecha y permitir UPSERT desde la web.
    // ------------------------------------------------------------------
    if (!indiceExiste($conexion, "Predicciones_Meteorologicas", "uq_predicciones_fecha_hora")) {
        $conexion->exec(
            "DELETE p1 FROM Predicciones_Meteorologicas p1
             INNER JOIN Predicciones_Meteorologicas p2
               ON p1.Fecha_hora = p2.Fecha_hora
              AND p1.Precipitacion_Id < p2.Precipitacion_Id"
        );
        $conexion->exec(
            "ALTER TABLE Predicciones_Meteorologicas
             ADD UNIQUE KEY uq_predicciones_fecha_hora (Fecha_hora)"
        );
        mostrar("Duplicados eliminados e índice único creado en Fecha_hora.");
    }

    // ------------------------------------------------------------------
    // 3. Índice para consultar mediciones por fecha al reentrenar.
    // ------------------------------------------------------------------
    if (
        tablaExiste($conexion, "Mediciones")
        && !indiceExiste($conexion, "Mediciones", "idx_mediciones_fecha")
    ) {
        $conexion->exec(
            "ALTER TABLE Mediciones ADD KEY idx_mediciones_fecha (Fecha_hora)"
        );
        mostrar("Índice idx_mediciones_fecha creado en Mediciones.");
    }

    // ------------------------------------------------------------------
    // 4. Predicciones diarias de energía.
    // ------------------------------------------------------------------
    $conexion->exec(
        "CREATE TABLE IF NOT EXISTS Predicciones_Energia (
            Prediccion_Id INT NOT NULL AUTO_INCREMENT,
            Fecha DATE NOT NULL,
            Energia_kwh DECIMAL(10,3) NOT NULL,
            Potencia_pico_w DECIMAL(10,2) NOT NULL DEFAULT 0,
            Hora_pico TIME NULL DEFAULT NULL,
            Gti_max_wm2 DECIMAL(8,2) NULL DEFAULT NULL,
            Temperatura_max_c DECIMAL(5,2) NULL DEFAULT NULL,
            Nubosidad_media DECIMAL(5,2) NULL DEFAULT NULL,
            Confianza_pct DECIMAL(5,2) NULL DEFAULT NULL,
            Incertidumbre_w DECIMAL(10,2) NULL DEFAULT NULL,
            Modelo_id VARCHAR(64) NULL DEFAULT NULL,
            Origen_modelo VARCHAR(32) NULL DEFAULT NULL,
            R2 DECIMAL(8,5) NULL DEFAULT NULL,
            Mae_w DECIMAL(10,2) NULL DEFAULT NULL,
            Creado_en DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
            Actualizado_en DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
                ON UPDATE CURRENT_TIMESTAMP,
            PRIMARY KEY (Prediccion_Id),
            UNIQUE KEY uq_prediccion_fecha (Fecha),
            KEY idx_prediccion_fecha (Fecha)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci"
    );

    // ------------------------------------------------------------------
    // 5. Detalle horario de la predicción (para gráficos y auditoría).
    // ------------------------------------------------------------------
    $conexion->exec(
        "CREATE TABLE IF NOT EXISTS Predicciones_Energia_Horaria (
            Prediccion_hora_Id INT NOT NULL AUTO_INCREMENT,
            Fecha_hora DATETIME NOT NULL,
            Potencia_w DECIMAL(10,2) NOT NULL DEFAULT 0,
            Energia_kwh DECIMAL(10,3) NOT NULL DEFAULT 0,
            Gti_wm2 DECIMAL(8,2) NULL DEFAULT NULL,
            Temperatura_c DECIMAL(5,2) NULL DEFAULT NULL,
            Nubosidad_pct DECIMAL(5,2) NULL DEFAULT NULL,
            Viento_kmh DECIMAL(6,2) NULL DEFAULT NULL,
            Creado_en DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
            Actualizado_en DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
                ON UPDATE CURRENT_TIMESTAMP,
            PRIMARY KEY (Prediccion_hora_Id),
            UNIQUE KEY uq_prediccion_hora (Fecha_hora),
            KEY idx_prediccion_hora_fecha (Fecha_hora)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci"
    );

    // ------------------------------------------------------------------
    // 6. Historial de entrenamientos (aprendizaje continuo).
    // ------------------------------------------------------------------
    $conexion->exec(
        "CREATE TABLE IF NOT EXISTS Historial_Entrenamiento (
            Entrenamiento_Id INT NOT NULL AUTO_INCREMENT,
            Fecha DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
            Origen VARCHAR(32) NOT NULL,
            Fuente VARCHAR(32) NULL DEFAULT NULL,
            Muestras_entrenamiento INT NULL DEFAULT NULL,
            Muestras_prueba INT NULL DEFAULT NULL,
            Muestras_reales INT NULL DEFAULT NULL,
            R2 DECIMAL(8,5) NULL DEFAULT NULL,
            Mae_w DECIMAL(10,2) NULL DEFAULT NULL,
            Rmse_w DECIMAL(10,2) NULL DEFAULT NULL,
            Modelo_id VARCHAR(64) NULL DEFAULT NULL,
            Ruta_modelo VARCHAR(255) NULL DEFAULT NULL,
            Detalle TEXT NULL DEFAULT NULL,
            Creado_en DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (Entrenamiento_Id),
            KEY idx_entrenamiento_fecha (Fecha)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci"
    );

    mostrar("Migración completada correctamente.");
} catch (Throwable $e) {
    if (!$esCLI) {
        http_response_code(500);
        echo "<h2>Error en la migración</h2>";
    }
    mostrar("Error: " . $e->getMessage());
    if ($esCLI) {
        exit(1);
    }
}

if (!$esCLI) {
    echo "</body></html>";
}
