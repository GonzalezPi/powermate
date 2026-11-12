<?php

/**
 * Tarea programada: actualizar clima, reentrenar si toca y predecir.
 *
 * Ejemplos:
 *     php cron_actualizar.php
 *     php cron_actualizar.php --sin-predecir
 *     php cron_actualizar.php --solo-predecir
 *     php cron_actualizar.php --reentrenar --json
 *
 * En cron (cada hora):
 *     0 * * * * /opt/lampp/bin/php /ruta/powermate-main/cron_actualizar.php >> /tmp/powermate.log 2>&1
 */

declare(strict_types=1);

if (PHP_SAPI !== "cli") {
    http_response_code(403);
    exit("Este script solo se puede ejecutar por consola.\n");
}

['conexion' => $conexion, 'config' => $config] =
    require __DIR__ . "/config/bootstrap.php";

$opciones = getopt("", [
    "solo-predecir",
    "sin-predecir",
    "reentrenar",
    "json",
    "verbose",
]);
$soloPredecir = isset($opciones["solo-predecir"]);
$sinPredecir = isset($opciones["sin-predecir"]);
$forzarEntrenamiento = isset($opciones["reentrenar"]);
$salidaJson = isset($opciones["json"]);

$bloqueo = fopen(
    sys_get_temp_dir() . "/powermate_ia.lock",
    "c"
);
if ($bloqueo === false || !flock($bloqueo, LOCK_EX | LOCK_NB)) {
    fwrite(STDERR, "Ya hay otra ejecución de la IA en curso.\n");
    exit(2);
}

$resultado = [
    "ok" => true,
    "clima" => null,
    "entrenamiento" => null,
    "prediccion" => null,
];

try {
    $repositorioClima = new RepositorioClima($conexion, $config);
    $ia = new SolarAI($config);
    $aprendizaje = new Aprendizaje(
        $conexion,
        $config,
        $repositorioClima,
        $ia
    );

    if (!$soloPredecir) {
        $pronostico = obtenerPronosticoMeteorologico($config);
        $resultado["clima"] = $repositorioClima->guardarPronostico(
            $pronostico
        );
    }

    if ($forzarEntrenamiento) {
        $resultado["entrenamiento"] = $aprendizaje->reentrenar("cron");
    } elseif (!$soloPredecir) {
        $resultado["entrenamiento"] =
            $aprendizaje->reentrenarSiCorresponde();
    }

    if (!$sinPredecir) {
        $resultado["prediccion"] = Prediccion::generar(
            $conexion,
            $config
        );
    }
} catch (Throwable $e) {
    $resultado["ok"] = false;
    $resultado["error"] = $e->getMessage();
    fwrite(STDERR, "Error: " . $e->getMessage() . "\n");
    if ($salidaJson) {
        echo json_encode(
            $resultado,
            JSON_UNESCAPED_UNICODE | JSON_UNESCAPED_SLASHES
        ) . "\n";
    }
    exit(1);
} finally {
    flock($bloqueo, LOCK_UN);
    fclose($bloqueo);
}

if ($salidaJson) {
    echo json_encode(
        $resultado,
        JSON_UNESCAPED_UNICODE
        | JSON_UNESCAPED_SLASHES
        | JSON_PRETTY_PRINT
    ) . "\n";
    exit(0);
}

if ($resultado["clima"] !== null) {
    printf(
        "Clima: %d horas (%d nuevas, %d actualizadas).\n",
        $resultado["clima"]["procesados"],
        $resultado["clima"]["insertados"],
        $resultado["clima"]["actualizados"]
    );
}
if ($resultado["entrenamiento"] !== null) {
    $metricas = $resultado["entrenamiento"]["metrics"] ?? [];
    printf(
        "Modelo reentrenado (%s): R2 %s · MAE %s W.\n",
        $resultado["entrenamiento"]["source"] ?? "?",
        $metricas["r2"] ?? "?",
        $metricas["mae_w"] ?? "?"
    );
} else {
    echo "Modelo: sin cambios (no hay mediciones nuevas suficientes).\n";
}
if ($resultado["prediccion"] !== null) {
    $resumen = $resultado["prediccion"]["resultado"]["summary"] ?? [];
    printf(
        "Predicción: %.2f kWh en %d días · hoy %.2f kWh.\n",
        (float) ($resumen["total_kwh"] ?? 0.0),
        (int) ($resumen["days"] ?? 0),
        (float) ($resumen["today_kwh"] ?? 0.0)
    );
}
