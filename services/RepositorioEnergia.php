<?php

/**
 * Persistencia de las predicciones de energía generadas por la IA.
 */

declare(strict_types=1);

class RepositorioEnergia
{
    private PDO $conexion;
    private array $config;
    private DateTimeZone $zonaHoraria;

    public function __construct(PDO $conexion, array $config)
    {
        $this->conexion = $conexion;
        $this->config = $config;
        $this->zonaHoraria = new DateTimeZone(
            (string) ($config["sitio"]["zona_horaria"] ?? "UTC")
        );
    }

    /**
     * Guarda el detalle diario y horario de una predicción.
     *
     * @param array<string, mixed> $resultado Respuesta del puente de IA.
     * @return array{diarias:int, horarias:int}
     */
    public function guardarPrediccion(array $resultado): array
    {
        $diarias = $resultado["daily"] ?? [];
        $horas = $resultado["hours"] ?? [];
        if (!is_array($diarias) || $diarias === []) {
            throw new RuntimeException(
                "La IA no devolvió predicciones diarias."
            );
        }

        $modelo = $resultado["summary"]["model"] ?? [];
        $modeloId = (string) ($resultado["model_key"] ?? "");
        $origen = $modelo["source"] ?? null;
        $r2 = $this->flotante($modelo["r2"] ?? null);
        $mae = $this->flotante($modelo["mae_w"] ?? null);
        $incertidumbre = $this->flotante(
            $modelo["uncertainty_95_w"] ?? null
        );
        $confianza = $r2 === null
            ? null
            : max(0.0, min(100.0, $r2 * 100.0));

        $sqlDiaria = "INSERT INTO Predicciones_Energia
                (Fecha, Energia_kwh, Potencia_pico_w, Hora_pico,
                 Gti_max_wm2, Temperatura_max_c, Nubosidad_media,
                 Confianza_pct, Incertidumbre_w, Modelo_id,
                 Origen_modelo, R2, Mae_w)
            VALUES
                (:fecha, :energia, :pico, :hora_pico,
                 :gti, :temp, :nubes,
                 :confianza, :incertidumbre, :modelo,
                 :origen, :r2, :mae)
            ON DUPLICATE KEY UPDATE
                Energia_kwh = VALUES(Energia_kwh),
                Potencia_pico_w = VALUES(Potencia_pico_w),
                Hora_pico = VALUES(Hora_pico),
                Gti_max_wm2 = VALUES(Gti_max_wm2),
                Temperatura_max_c = VALUES(Temperatura_max_c),
                Nubosidad_media = VALUES(Nubosidad_media),
                Confianza_pct = VALUES(Confianza_pct),
                Incertidumbre_w = VALUES(Incertidumbre_w),
                Modelo_id = VALUES(Modelo_id),
                Origen_modelo = VALUES(Origen_modelo),
                R2 = VALUES(R2),
                Mae_w = VALUES(Mae_w)";

        $sqlHoraria = "INSERT INTO Predicciones_Energia_Horaria
                (Fecha_hora, Potencia_w, Energia_kwh, Gti_wm2,
                 Temperatura_c, Nubosidad_pct, Viento_kmh)
            VALUES
                (:fecha_hora, :potencia, :energia, :gti,
                 :temperatura, :nubosidad, :viento)
            ON DUPLICATE KEY UPDATE
                Potencia_w = VALUES(Potencia_w),
                Energia_kwh = VALUES(Energia_kwh),
                Gti_wm2 = VALUES(Gti_wm2),
                Temperatura_c = VALUES(Temperatura_c),
                Nubosidad_pct = VALUES(Nubosidad_pct),
                Viento_kmh = VALUES(Viento_kmh)";

        $consultaDiaria = $this->conexion->prepare($sqlDiaria);
        $consultaHoraria = $this->conexion->prepare($sqlHoraria);

        $this->conexion->beginTransaction();
        try {
            $fechas = [];
            foreach ($diarias as $fila) {
                if (is_array($fila) && !empty($fila["date"])) {
                    $fechas[] = (string) $fila["date"];
                }
            }
            if ($fechas !== []) {
                // Elimina predicciones futuras obsoletas cuando el horizonte
                // del pronóstico cambia.
                $limpiezaDiaria = $this->conexion->prepare(
                    "DELETE FROM Predicciones_Energia WHERE Fecha >= :desde"
                );
                $limpiezaDiaria->execute([":desde" => min($fechas)]);
            }

            $guardadas = 0;
            foreach ($diarias as $fila) {
                if (!is_array($fila) || empty($fila["date"])) {
                    continue;
                }
                $consultaDiaria->execute([
                    ":fecha" => (string) $fila["date"],
                    ":energia" => $this->flotante(
                        $fila["energy_kwh"] ?? null,
                        0.0
                    ),
                    ":pico" => $this->flotante(
                        $fila["peak_w"] ?? null,
                        0.0
                    ),
                    ":hora_pico" => $this->hora(
                        $fila["peak_time"] ?? null
                    ),
                    ":gti" => $this->flotante(
                        $fila["gti_max_wm2"] ?? null
                    ),
                    ":temp" => $this->flotante(
                        $fila["temp_max_c"] ?? null
                    ),
                    ":nubes" => $this->flotante(
                        $fila["cloud_mean_pct"] ?? null
                    ),
                    ":confianza" => $confianza,
                    ":incertidumbre" => $incertidumbre,
                    ":modelo" => $modeloId !== "" ? $modeloId : null,
                    ":origen" => $origen,
                    ":r2" => $r2,
                    ":mae" => $mae,
                ]);
                $guardadas++;
            }

            // El detalle horario se reemplaza para evitar horas obsoletas
            // cuando cambia el horizonte del pronóstico.
            $horarias = 0;
            if (is_array($horas) && $horas !== []) {
                $primera = new DateTimeImmutable(
                    (string) ($horas[0]["time"] ?? "now")
                );
                $desde = $primera
                    ->setTimezone($this->zonaHoraria)
                    ->setTime(0, 0)
                    ->format("Y-m-d H:i:s");
                $limpieza = $this->conexion->prepare(
                    "DELETE FROM Predicciones_Energia_Horaria
                     WHERE Fecha_hora >= :desde"
                );
                $limpieza->execute([":desde" => $desde]);

                foreach ($horas as $fila) {
                    if (!is_array($fila) || empty($fila["time"])) {
                        continue;
                    }
                    $fechaHora = (new DateTimeImmutable(
                        (string) $fila["time"]
                    ))
                        ->setTimezone($this->zonaHoraria)
                        ->format("Y-m-d H:i:s");

                    $consultaHoraria->execute([
                        ":fecha_hora" => $fechaHora,
                        ":potencia" => $this->flotante(
                            $fila["power_w"] ?? null,
                            0.0
                        ),
                        ":energia" => $this->flotante(
                            $fila["energy_kwh"] ?? null,
                            0.0
                        ),
                        ":gti" => $this->flotante(
                            $fila["gti_wm2"] ?? null
                        ),
                        ":temperatura" => $this->flotante(
                            $fila["temperature_c"] ?? null
                        ),
                        ":nubosidad" => $this->flotante(
                            $fila["cloud_cover_pct"] ?? null
                        ),
                        ":viento" => $this->flotante(
                            $fila["wind_kmh"] ?? null
                        ),
                    ]);
                    $horarias++;
                }
            }

            $this->conexion->commit();
        } catch (Throwable $e) {
            if ($this->conexion->inTransaction()) {
                $this->conexion->rollBack();
            }
            throw $e;
        }

        return ["diarias" => $guardadas, "horarias" => $horarias];
    }

    /**
     * @return list<array<string, mixed>>
     */
    public function obtenerDiarias(
        DateTimeImmutable $desde,
        int $limite = 10
    ): array {
        $limite = max(1, min(60, $limite));
        $sql = "SELECT Fecha, Energia_kwh, Potencia_pico_w, Hora_pico,
                       Gti_max_wm2, Temperatura_max_c, Nubosidad_media,
                       Confianza_pct, Incertidumbre_w, Modelo_id,
                       Origen_modelo, R2, Mae_w, Actualizado_en
                FROM Predicciones_Energia
                WHERE Fecha >= :desde
                ORDER BY Fecha ASC
                LIMIT $limite";
        $consulta = $this->conexion->prepare($sql);
        $consulta->execute([":desde" => $desde->format("Y-m-d")]);
        return $consulta->fetchAll();
    }

    public function resumenProximosDias(DateTimeImmutable $desde): array
    {
        $consulta = $this->conexion->prepare(
            "SELECT COUNT(*) AS dias,
                    COALESCE(SUM(Energia_kwh), 0) AS energia,
                    COALESCE(MAX(Potencia_pico_w), 0) AS pico
             FROM Predicciones_Energia
             WHERE Fecha >= :desde"
        );
        $consulta->execute([":desde" => $desde->format("Y-m-d")]);
        $fila = $consulta->fetch() ?: [];
        return [
            "dias" => (int) ($fila["dias"] ?? 0),
            "energia" => (float) ($fila["energia"] ?? 0.0),
            "pico" => (float) ($fila["pico"] ?? 0.0),
        ];
    }

    public function ultimaPrediccion(): ?array
    {
        $fila = $this->conexion
            ->query(
                "SELECT Fecha, Energia_kwh, Modelo_id, Origen_modelo,
                        R2, Mae_w, Confianza_pct, Incertidumbre_w,
                        Actualizado_en
                 FROM Predicciones_Energia
                 ORDER BY Actualizado_en DESC
                 LIMIT 1"
            )
            ->fetch();
        return $fila ?: null;
    }

    private function flotante(mixed $valor, ?float $defecto = null): ?float
    {
        if ($valor === null || $valor === "" || !is_numeric($valor)) {
            return $defecto;
        }
        return (float) $valor;
    }

    private function hora(mixed $valor): ?string
    {
        if (!is_string($valor) || trim($valor) === "") {
            return null;
        }
        // Acepta "15:00" o un ISO completo; MySQL espera HH:MM:SS.
        if (preg_match('/^\d{2}:\d{2}$/', $valor) === 1) {
            return $valor . ":00";
        }
        if (preg_match('/^\d{2}:\d{2}:\d{2}$/', $valor) === 1) {
            return $valor;
        }
        try {
            return (new DateTimeImmutable($valor))->format("H:i:s");
        } catch (Throwable $e) {
            return null;
        }
    }
}
