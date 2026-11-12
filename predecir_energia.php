<?php

/**
 * Genera la predicción de energía diaria a partir del clima almacenado.
 */

declare(strict_types=1);

['conexion' => $conexion, 'config' => $config] =
    require __DIR__ . "/config/bootstrap.php";

try {
    $salida = Prediccion::generar($conexion, $config);
    $resultado = $salida["resultado"];
    $resumen = $resultado["summary"] ?? [];

    $mensaje = sprintf(
        "Predicción actualizada: %.2f kWh en %d días · hoy %.2f kWh · "
        . "pico %.2f kW.",
        (float) ($resumen["total_kwh"] ?? 0.0),
        (int) ($resumen["days"] ?? 0),
        (float) ($resumen["today_kwh"] ?? 0.0),
        (float) ($resumen["peak_w"] ?? 0.0) / 1000.0
    );

    if ($salida["reentrenado"] !== null) {
        $mensaje .= " El modelo se reentrenó automáticamente.";
    }

    if (quiereJson()) {
        responderJson([
            "ok" => true,
            "prediccion" => $resultado,
            "guardado" => $salida["guardado"],
            "reentrenado" => $salida["reentrenado"],
            "mensaje" => $mensaje,
        ]);
    }

    redirigirConMensaje($mensaje, "ok");
} catch (Throwable $e) {
    error_log("[PowerMate] Error al predecir energía: " . $e->getMessage());

    if (quiereJson()) {
        responderJson(
            ["ok" => false, "error" => $e->getMessage()],
            500
        );
    }

    redirigirConMensaje(
        "Error al generar la predicción: " . $e->getMessage(),
        "error"
    );
}
