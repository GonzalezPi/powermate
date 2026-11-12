<?php

/**
 * Descarga el pronóstico de Open-Meteo, lo guarda en MySQL y, si está
 * configurado, lanza una predicción de energía con la IA.
 */

declare(strict_types=1);

['conexion' => $conexion, 'config' => $config] =
    require __DIR__ . "/config/bootstrap.php";

try {
    $pronostico = obtenerPronosticoMeteorologico($config);
    $repositorio = new RepositorioClima($conexion, $config);
    $guardado = $repositorio->guardarPronostico($pronostico);

    $prediccion = null;
    $errorPrediccion = null;
    if (!empty($config["pronostico"]["predecir_tras_actualizar"])) {
        try {
            $prediccion = Prediccion::generar($conexion, $config);
        } catch (Throwable $e) {
            $errorPrediccion = $e->getMessage();
            error_log(
                "[PowerMate] No se pudo predecir tras actualizar: "
                . $e->getMessage()
            );
        }
    }

    $mensaje = sprintf(
        "Clima actualizado: %d horas procesadas (%d nuevas, %d actualizadas).",
        $guardado["procesados"],
        $guardado["insertados"],
        $guardado["actualizados"]
    );

    if ($prediccion !== null) {
        $resumen = $prediccion["resultado"]["summary"] ?? [];
        $mensaje .= sprintf(
            " Predicción generada: %.2f kWh en %d días.",
            (float) ($resumen["total_kwh"] ?? 0.0),
            (int) ($resumen["days"] ?? 0)
        );
    }
    if ($errorPrediccion !== null) {
        $mensaje .= " No se pudo predecir: " . $errorPrediccion;
    }

    if (quiereJson()) {
        responderJson([
            "ok" => true,
            "guardado" => $guardado,
            "prediccion" => $prediccion["resultado"] ?? null,
            "error_prediccion" => $errorPrediccion,
            "mensaje" => $mensaje,
        ]);
    }

    redirigirConMensaje(
        $mensaje,
        $errorPrediccion === null ? "ok" : "aviso"
    );
} catch (Throwable $e) {
    error_log(
        "[PowerMate] Error al guardar predicciones: " . $e->getMessage()
    );

    if (quiereJson()) {
        responderJson(
            ["ok" => false, "error" => $e->getMessage()],
            500
        );
    }

    redirigirConMensaje(
        "Error al actualizar el clima: " . $e->getMessage(),
        "error"
    );
}
