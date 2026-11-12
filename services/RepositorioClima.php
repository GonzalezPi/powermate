<?php

/**
 * Acceso a los datos meteorológicos almacenados en MySQL.
 *
 * Traduce las columnas en español de la base de datos a las claves que espera
 * el modelo de IA y convierte las fechas locales a UTC.
 */

declare(strict_types=1);

class RepositorioClima
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
     * Guarda (inserta o actualiza) el pronóstico horario.
     *
     * @param array{zona_horaria:string, horas:array<string, array<int, mixed>>} $pronostico
     * @return array{procesados:int, insertados:int, actualizados:int}
     */
    public function guardarPronostico(array $pronostico): array
    {
        $horas = $pronostico["horas"] ?? [];
        $tiempos = $horas["time"] ?? [];
        $cantidad = is_array($tiempos) ? count($tiempos) : 0;
        if ($cantidad === 0) {
            throw new Exception("Open-Meteo no devolvió horas para guardar.");
        }

        foreach (variablesMeteorologicas() as $variable) {
            if (
                isset($horas[$variable])
                && count($horas[$variable]) !== $cantidad
            ) {
                throw new Exception(
                    "Los datos de '$variable' no tienen la misma cantidad "
                    . "de registros que las horas."
                );
            }
        }

        $sql = "INSERT INTO Predicciones_Meteorologicas
                    (Fecha_hora, Temperatura, Precipitaciones, Consumo,
                     Humedad_relativa, Nubosidad, Viento,
                     Radiacion_global, Radiacion_directa, Radiacion_difusa,
                     Radiacion_inclinada, Nieve, Probabilidad_precipitacion,
                     Codigo_tiempo, Es_dia, Origen)
                VALUES
                    (:fecha, :temperatura, :precipitaciones, 0,
                     :humedad, :nubosidad, :viento,
                     :radiacion_global, :radiacion_directa, :radiacion_difusa,
                     :radiacion_inclinada, :nieve, :probabilidad,
                     :codigo, :es_dia, :origen)
                ON DUPLICATE KEY UPDATE
                    Temperatura = VALUES(Temperatura),
                    Precipitaciones = VALUES(Precipitaciones),
                    Humedad_relativa = VALUES(Humedad_relativa),
                    Nubosidad = VALUES(Nubosidad),
                    Viento = VALUES(Viento),
                    Radiacion_global = VALUES(Radiacion_global),
                    Radiacion_directa = VALUES(Radiacion_directa),
                    Radiacion_difusa = VALUES(Radiacion_difusa),
                    Radiacion_inclinada = VALUES(Radiacion_inclinada),
                    Nieve = VALUES(Nieve),
                    Probabilidad_precipitacion =
                        VALUES(Probabilidad_precipitacion),
                    Codigo_tiempo = VALUES(Codigo_tiempo),
                    Es_dia = VALUES(Es_dia),
                    Origen = VALUES(Origen)";

        $consulta = $this->conexion->prepare($sql);
        $insertados = 0;
        $actualizados = 0;
        $procesados = 0;

        $this->conexion->beginTransaction();
        try {
            for ($i = 0; $i < $cantidad; $i++) {
                $hora = $tiempos[$i] ?? null;
                if (!is_string($hora) || $hora === "") {
                    continue;
                }

                $fechaLocal = new DateTimeImmutable($hora, $this->zonaHoraria);

                $consulta->execute([
                    ":fecha" => $fechaLocal->format("Y-m-d H:i:s"),
                    ":temperatura" => $this->numero(
                        $horas["temperature_2m"][$i] ?? null,
                        0.0
                    ),
                    ":precipitaciones" => $this->numero(
                        $horas["rain"][$i] ?? null,
                        0.0
                    ),
                    ":humedad" => $this->numero(
                        $horas["relative_humidity_2m"][$i] ?? null
                    ),
                    ":nubosidad" => $this->numero(
                        $horas["cloud_cover"][$i] ?? null
                    ),
                    ":viento" => $this->numero(
                        $horas["wind_speed_10m"][$i] ?? null
                    ),
                    ":radiacion_global" => $this->numero(
                        $horas["shortwave_radiation"][$i] ?? null
                    ),
                    ":radiacion_directa" => $this->numero(
                        $horas["direct_normal_irradiance"][$i] ?? null
                    ),
                    ":radiacion_difusa" => $this->numero(
                        $horas["diffuse_radiation"][$i] ?? null
                    ),
                    ":radiacion_inclinada" => $this->numero(
                        $horas["global_tilted_irradiance"][$i] ?? null
                    ),
                    ":nieve" => $this->numero(
                        $horas["snow_depth"][$i] ?? null
                    ),
                    ":probabilidad" => $this->numero(
                        $horas["precipitation_probability"][$i] ?? null
                    ),
                    ":codigo" => $this->entero(
                        $horas["weather_code"][$i] ?? null
                    ),
                    ":es_dia" => $this->entero(
                        $horas["is_day"][$i] ?? null
                    ),
                    ":origen" => "forecast",
                ]);

                $filas = $consulta->rowCount();
                if ($filas === 1) {
                    $insertados++;
                } elseif ($filas === 2) {
                    $actualizados++;
                }
                $procesados++;
            }

            $this->conexion->commit();
        } catch (Throwable $e) {
            if ($this->conexion->inTransaction()) {
                $this->conexion->rollBack();
            }
            throw $e;
        }

        return [
            "procesados" => $procesados,
            "insertados" => $insertados,
            "actualizados" => $actualizados,
        ];
    }

    /**
     * Horas con radiación disponible a partir de una fecha local.
     *
     * @return list<array<string, mixed>>
     */
    public function obtenerPronostico(
        DateTimeImmutable $desde,
        ?DateTimeImmutable $hasta = null
    ): array {
        $sql = "SELECT Fecha_hora, Temperatura, Precipitaciones,
                       Humedad_relativa, Nubosidad, Viento,
                       Radiacion_global, Radiacion_directa, Radiacion_difusa,
                       Radiacion_inclinada, Nieve, Codigo_tiempo, Es_dia
                FROM Predicciones_Meteorologicas
                WHERE Fecha_hora >= :desde
                  AND Radiacion_inclinada IS NOT NULL
                  AND Radiacion_global IS NOT NULL";
        $parametros = [
            ":desde" => $desde->format("Y-m-d H:i:s"),
        ];
        if ($hasta !== null) {
            $sql .= " AND Fecha_hora <= :hasta";
            $parametros[":hasta"] = $hasta->format("Y-m-d H:i:s");
        }
        $sql .= " ORDER BY Fecha_hora ASC";

        $consulta = $this->conexion->prepare($sql);
        $consulta->execute($parametros);
        return array_map([$this, "filaAPayload"], $consulta->fetchAll());
    }

    /**
     * Histórico completo (o por rango) para entrenar el modelo.
     *
     * @return list<array<string, mixed>>
     */
    public function obtenerHistorico(
        ?DateTimeImmutable $desde = null,
        ?DateTimeImmutable $hasta = null
    ): array {
        $sql = "SELECT Fecha_hora, Temperatura, Precipitaciones,
                       Humedad_relativa, Nubosidad, Viento,
                       Radiacion_global, Radiacion_directa, Radiacion_difusa,
                       Radiacion_inclinada, Nieve, Codigo_tiempo, Es_dia
                FROM Predicciones_Meteorologicas
                WHERE Radiacion_inclinada IS NOT NULL
                  AND Radiacion_global IS NOT NULL";
        $parametros = [];
        if ($desde !== null) {
            $sql .= " AND Fecha_hora >= :desde";
            $parametros[":desde"] = $desde->format("Y-m-d H:i:s");
        }
        if ($hasta !== null) {
            $sql .= " AND Fecha_hora <= :hasta";
            $parametros[":hasta"] = $hasta->format("Y-m-d H:i:s");
        }
        $sql .= " ORDER BY Fecha_hora ASC";

        $consulta = $this->conexion->prepare($sql);
        $consulta->execute($parametros);
        return array_map([$this, "filaAPayload"], $consulta->fetchAll());
    }

    public function contarRegistros(): int
    {
        return (int) $this->conexion
            ->query("SELECT COUNT(*) FROM Predicciones_Meteorologicas")
            ->fetchColumn();
    }

    public function contarConRadiacion(): int
    {
        return (int) $this->conexion
            ->query(
                "SELECT COUNT(*) FROM Predicciones_Meteorologicas
                 WHERE Radiacion_inclinada IS NOT NULL"
            )
            ->fetchColumn();
    }

    public function ultimaActualizacion(): ?string
    {
        $valor = $this->conexion
            ->query("SELECT MAX(Actualizado_en) FROM Predicciones_Meteorologicas")
            ->fetchColumn();
        return $valor === false || $valor === null ? null : (string) $valor;
    }

    public function rangoDisponible(): ?array
    {
        $fila = $this->conexion
            ->query(
                "SELECT MIN(Fecha_hora) AS inicio, MAX(Fecha_hora) AS fin
                 FROM Predicciones_Meteorologicas
                 WHERE Radiacion_inclinada IS NOT NULL"
            )
            ->fetch();
        if (!$fila || $fila["inicio"] === null) {
            return null;
        }
        return ["inicio" => $fila["inicio"], "fin" => $fila["fin"]];
    }

    public function zonaHoraria(): DateTimeZone
    {
        return $this->zonaHoraria;
    }

    // ------------------------------------------------------------------
    // Conversión de filas
    // ------------------------------------------------------------------
    private function filaAPayload(array $fila): array
    {
        $fechaLocal = new DateTimeImmutable(
            (string) $fila["Fecha_hora"],
            $this->zonaHoraria
        );
        $fechaUtc = $fechaLocal
            ->setTimezone(new DateTimeZone("UTC"))
            ->format("Y-m-d\TH:i:s\Z");

        return [
            "time_utc" => $fechaUtc,
            "temperature_2m" => $this->flotante($fila["Temperatura"]),
            "precipitation" => $this->flotante($fila["Precipitaciones"]),
            "relative_humidity_2m" => $this->flotante(
                $fila["Humedad_relativa"]
            ),
            "cloud_cover" => $this->flotante($fila["Nubosidad"]),
            "wind_speed_10m" => $this->flotante($fila["Viento"]),
            "shortwave_radiation" => $this->flotante(
                $fila["Radiacion_global"]
            ),
            "direct_normal_irradiance" => $this->flotante(
                $fila["Radiacion_directa"]
            ),
            "diffuse_radiation" => $this->flotante(
                $fila["Radiacion_difusa"]
            ),
            "global_tilted_irradiance" => $this->flotante(
                $fila["Radiacion_inclinada"]
            ),
            "snow_depth" => $this->flotante($fila["Nieve"]),
            "weather_code" => $this->entero($fila["Codigo_tiempo"]),
            "is_day" => $this->entero($fila["Es_dia"]),
        ];
    }

    private function numero(mixed $valor, ?float $porDefecto = null): ?float
    {
        if ($valor === null || $valor === "" || !is_numeric($valor)) {
            return $porDefecto;
        }
        return (float) $valor;
    }

    private function flotante(mixed $valor): ?float
    {
        if ($valor === null || $valor === "") {
            return null;
        }
        return is_numeric($valor) ? (float) $valor : null;
    }

    private function entero(mixed $valor): ?int
    {
        if ($valor === null || $valor === "") {
            return null;
        }
        return is_numeric($valor) ? (int) $valor : null;
    }
}
