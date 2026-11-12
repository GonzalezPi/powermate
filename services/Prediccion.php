<?php

/**
 * Orquestador de la predicción: clima almacenado → IA → MySQL.
 */

declare(strict_types=1);

class Prediccion
{
    /**
     * Genera y guarda la predicción de energía.
     *
     * Antes de predecir comprueba si hay mediciones nuevas suficientes para
     * reentrenar. Un fallo al reentrenar no impide predecir con el modelo
     * actual.
     *
     * @return array<string, mixed>
     */
    public static function generar(PDO $conexion, array $config): array
    {
        $repoClima = new RepositorioClima($conexion, $config);
        $ia = new SolarAI($config);
        $aprendizaje = new Aprendizaje(
            $conexion,
            $config,
            $repoClima,
            $ia
        );

        $reentrenado = null;
        try {
            $reentrenado = $aprendizaje->reentrenarSiCorresponde();
        } catch (Throwable $e) {
            error_log(
                "[PowerMate IA] No se pudo reentrenar: " . $e->getMessage()
            );
        }

        $zona = $repoClima->zonaHoraria();
        $desde = new DateTimeImmutable("today", $zona);
        $clima = $repoClima->obtenerPronostico($desde);

        if ($clima === []) {
            throw new RuntimeException(
                "No hay datos meteorológicos con radiación en la base de "
                . "datos. Pulsa 'Actualizar clima' para descargar el "
                . "pronóstico y vuelve a intentarlo."
            );
        }

        $resultado = $ia->predecir($clima);
        $repoEnergia = new RepositorioEnergia($conexion, $config);
        $guardado = $repoEnergia->guardarPrediccion($resultado);

        $modelo = $resultado["summary"]["model"] ?? [];
        $metricas = $resultado["metrics"] ?? [];
        $aprendizaje->registrarModeloInicial([
            "model_key" => $resultado["model_key"] ?? null,
            "model_path" => $resultado["model_path"] ?? null,
            "source" => $resultado["source"]
                ?? ($modelo["source"] ?? null),
            "data_range" => $resultado["data_range"]
                ?? ($modelo["data_range"] ?? null),
            "metrics" => [
                "r2" => $metricas["r2"] ?? ($modelo["r2"] ?? null),
                "mae_w" => $metricas["mae_w"]
                    ?? ($modelo["mae_w"] ?? null),
                "rmse_w" => $metricas["rmse_w"]
                    ?? ($modelo["rmse_w"] ?? null),
                "train_samples" => $metricas["train_samples"] ?? null,
                "test_samples" => $metricas["test_samples"] ?? null,
            ],
        ]);

        return [
            "resultado" => $resultado,
            "guardado" => $guardado,
            "reentrenado" => $reentrenado,
        ];
    }
}
