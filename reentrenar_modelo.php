<?php

/**
 * Reentrena el modelo de IA y, si es posible, regenera la predicción.
 */

declare(strict_types=1);

['conexion' => $conexion, 'config' => $config] =
    require __DIR__ . "/config/bootstrap.php";

try {
    $repositorioClima = new RepositorioClima($conexion, $config);
    $ia = new SolarAI($config);
    $aprendizaje = new Aprendizaje(
        $conexion,
        $config,
        $repositorioClima,
        $ia
    );

    $resultado = $aprendizaje->reentrenar("manual");
    $metricas = $resultado["metrics"] ?? [];

    $mensaje = sprintf(
        "Modelo reentrenado (%s): R² %.4f · MAE %.1f W · %d muestras.",
        (string) ($resultado["source"] ?? "desconocido"),
        (float) ($metricas["r2"] ?? 0.0),
        (float) ($metricas["mae_w"] ?? 0.0),
        (int) ($metricas["train_samples"] ?? 0)
    );

    $prediccion = null;
    $errorPrediccion = null;
    try {
        $prediccion = Prediccion::generar($conexion, $config);
        $resumen = $prediccion["resultado"]["summary"] ?? [];
        $mensaje .= sprintf(
            " Predicción regenerada: %.2f kWh en %d días.",
            (float) ($resumen["total_kwh"] ?? 0.0),
            (int) ($resumen["days"] ?? 0)
        );
    } catch (Throwable $e) {
        $errorPrediccion = $e->getMessage();
        error_log(
            "[PowerMate] Modelo reentrenado, pero falló la predicción: "
            . $e->getMessage()
        );
    }

    if (quiereJson()) {
        responderJson([
            "ok" => true,
            "entrenamiento" => $resultado,
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
    error_log("[PowerMate] Error al reentrenar: " . $e->getMessage());

    if (quiereJson()) {
        responderJson(
            ["ok" => false, "error" => $e->getMessage()],
            500
        );
    }

    redirigirConMensaje(
        "Error al reentrenar el modelo: " . $e->getMessage(),
        "error"
    );
}
