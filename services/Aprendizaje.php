<?php

/**
 * Aprendizaje continuo: decide cuándo reentrenar y guarda el historial.
 *
 * Fuentes de mejora:
 *  - Mediciones reales de producción (tabla Mediciones.Panel_solar).
 *  - Clima que la web va acumulando en Predicciones_Meteorologicas.
 *  - Histórico de Open-Meteo como respaldo inicial.
 */

declare(strict_types=1);

class Aprendizaje
{
    private PDO $conexion;
    private array $config;
    private RepositorioClima $clima;
    private SolarAI $ia;
    private DateTimeZone $zonaHoraria;

    public function __construct(
        PDO $conexion,
        array $config,
        RepositorioClima $clima,
        SolarAI $ia
    ) {
        $this->conexion = $conexion;
        $this->config = $config;
        $this->clima = $clima;
        $this->ia = $ia;
        $this->zonaHoraria = new DateTimeZone(
            (string) ($config["sitio"]["zona_horaria"] ?? "UTC")
        );
    }

    /**
     * Reentrena solo si hay suficientes mediciones nuevas.
     *
     * @return array<string, mixed>|null
     */
    public function reentrenarSiCorresponde(): ?array
    {
        if (empty($this->config["ia"]["auto_reentrenar"])) {
            return null;
        }

        $minimas = (int) (
            $this->config["ia"]["min_nuevas_mediciones"] ?? 48
        );
        $nuevas = $this->contarNuevasMediciones();
        if ($nuevas < $minimas) {
            return null;
        }

        return $this->reentrenar("auto");
    }

    /**
     * Fuerza un reentrenamiento y lo registra en el historial.
     *
     * @return array<string, mixed>
     */
    public function reentrenar(string $origen = "manual"): array
    {
        [$desde, $hasta] = $this->rangoMediciones();

        $climaDesde = null;
        $climaHasta = null;
        if ($desde !== null && $hasta !== null) {
            $climaDesde = $desde->modify("-1 day");
            $climaHasta = $hasta->modify("+1 day");
        }

        $clima = $this->clima->obtenerHistorico($climaDesde, $climaHasta);
        if ($clima === []) {
            // Puede que el clima almacenado no cubra el periodo medido.
            $clima = $this->clima->obtenerHistorico();
        }
        if ($clima === []) {
            throw new RuntimeException(
                "No hay datos meteorológicos históricos con radiación. "
                . "Actualiza el pronóstico varias veces para acumular "
                . "histórico antes de reentrenar."
            );
        }

        $mediciones = $this->obtenerMediciones($desde, $hasta);
        $resultado = $this->ia->entrenar(
            $clima,
            $mediciones,
            [
                "min_real_samples" => (int) (
                    $this->config["ia"]["min_muestras_reales"] ?? 100
                ),
            ]
        );

        $this->registrarEntrenamiento(
            $resultado,
            $origen,
            count($mediciones)
        );

        return $resultado;
    }

    /**
     * Registra en el historial el modelo que ya estaba entrenado, para que
     * la primera predicción deje constancia de su origen y métricas.
     *
     * @param array<string, mixed> $resultado
     */
    public function registrarModeloInicial(array $resultado): void
    {
        if ($this->ultimoEntrenamiento() !== null) {
            return;
        }
        $this->registrarEntrenamiento($resultado, "inicial", 0);
    }

    /**
     * @param array<string, mixed> $resultado
     */
    public function registrarEntrenamiento(
        array $resultado,
        string $origen,
        int $muestrasReales
    ): void {
        $metricas = $resultado["metrics"] ?? [];
        $sql = "INSERT INTO Historial_Entrenamiento
                    (Fecha, Origen, Fuente, Muestras_entrenamiento,
                     Muestras_prueba, Muestras_reales, R2, Mae_w, Rmse_w,
                     Modelo_id, Ruta_modelo, Detalle)
                VALUES
                    (NOW(), :origen, :fuente, :entrenamiento,
                     :prueba, :reales, :r2, :mae, :rmse,
                     :modelo, :ruta, :detalle)";
        $consulta = $this->conexion->prepare($sql);
        $consulta->execute([
            ":origen" => $origen,
            ":fuente" => $resultado["source"]
                ?? ($resultado["origin"] ?? null),
            ":entrenamiento" => $this->entero(
                $metricas["train_samples"] ?? null
            ),
            ":prueba" => $this->entero(
                $metricas["test_samples"] ?? null
            ),
            ":reales" => $muestrasReales,
            ":r2" => $this->flotante($metricas["r2"] ?? null),
            ":mae" => $this->flotante($metricas["mae_w"] ?? null),
            ":rmse" => $this->flotante($metricas["rmse_w"] ?? null),
            ":modelo" => $resultado["model_key"] ?? null,
            ":ruta" => $resultado["model_path"] ?? null,
            ":detalle" => json_encode(
                [
                    "origin" => $resultado["origin"] ?? null,
                    "data_range" => $resultado["data_range"] ?? null,
                    "extra" => $resultado["extra"] ?? [],
                ],
                JSON_UNESCAPED_UNICODE | JSON_UNESCAPED_SLASHES
            ),
        ]);
    }

    public function ultimoEntrenamiento(): ?array
    {
        $fila = $this->conexion
            ->query(
                "SELECT * FROM Historial_Entrenamiento
                 ORDER BY Fecha DESC, Entrenamiento_Id DESC
                 LIMIT 1"
            )
            ->fetch();
        return $fila ?: null;
    }

    /**
     * @return list<array<string, mixed>>
     */
    public function historial(int $limite = 6): array
    {
        $limite = max(1, min(50, $limite));
        return $this->conexion
            ->query(
                "SELECT * FROM Historial_Entrenamiento
                 ORDER BY Fecha DESC, Entrenamiento_Id DESC
                 LIMIT $limite"
            )
            ->fetchAll();
    }

    public function contarNuevasMediciones(): int
    {
        $ultimo = $this->ultimoEntrenamiento();
        $desde = $ultimo["Fecha"] ?? "1970-01-01 00:00:00";
        $consulta = $this->conexion->prepare(
            "SELECT COUNT(*) FROM Mediciones
             WHERE Panel_solar > 0 AND Fecha_hora > :desde"
        );
        $consulta->execute([":desde" => $desde]);
        return (int) $consulta->fetchColumn();
    }

    public function contarMediciones(): int
    {
        return (int) $this->conexion
            ->query(
                "SELECT COUNT(*) FROM Mediciones WHERE Panel_solar > 0"
            )
            ->fetchColumn();
    }

    public function resumen(): array
    {
        $ultimo = $this->ultimoEntrenamiento();
        return [
            "mediciones_totales" => $this->contarMediciones(),
            "mediciones_nuevas" => $this->contarNuevasMediciones(),
            "minimas_para_reentrenar" => (int) (
                $this->config["ia"]["min_nuevas_mediciones"] ?? 48
            ),
            "ultimo" => $ultimo,
            "historial" => $this->historial(6),
        ];
    }

    // ------------------------------------------------------------------
    // Mediciones reales
    // ------------------------------------------------------------------
    /**
     * @return list<array{time_utc:string, power_w:float}>
     */
    public function obtenerMediciones(
        ?DateTimeImmutable $desde = null,
        ?DateTimeImmutable $hasta = null
    ): array {
        $agregacion = strtolower(
            (string) ($this->config["mediciones"]["agregacion"] ?? "sum")
        );
        $funcion = match ($agregacion) {
            "mean", "media", "avg" => "AVG",
            "max", "maximo" => "MAX",
            default => "SUM",
        };

        $sql = "SELECT Fecha_hora, $funcion(Panel_solar) AS potencia
                FROM Mediciones
                WHERE Panel_solar IS NOT NULL";
        $parametros = [];
        if ($desde !== null) {
            $sql .= " AND Fecha_hora >= :desde";
            $parametros[":desde"] = $desde->format("Y-m-d H:i:s");
        }
        if ($hasta !== null) {
            $sql .= " AND Fecha_hora <= :hasta";
            $parametros[":hasta"] = $hasta->format("Y-m-d H:i:s");
        }
        $sql .= " GROUP BY Fecha_hora ORDER BY Fecha_hora ASC";

        $consulta = $this->conexion->prepare($sql);
        $consulta->execute($parametros);
        $filas = $consulta->fetchAll();
        if ($filas === []) {
            return [];
        }

        $valores = array_map(
            static fn (array $fila): float => (float) $fila["potencia"],
            $filas
        );
        $factor = $this->factorUnidad(max($valores));

        $resultado = [];
        foreach ($filas as $fila) {
            $fechaLocal = new DateTimeImmutable(
                (string) $fila["Fecha_hora"],
                $this->zonaHoraria
            );
            $resultado[] = [
                "time_utc" => $fechaLocal
                    ->setTimezone(new DateTimeZone("UTC"))
                    ->format("Y-m-d\TH:i:s\Z"),
                "power_w" => round((float) $fila["potencia"] * $factor, 2),
            ];
        }
        return $resultado;
    }

    private function factorUnidad(float $maximo): float
    {
        $unidad = strtolower(
            (string) ($this->config["mediciones"]["unidad"] ?? "auto")
        );
        if ($unidad === "kw" || $unidad === "kilowatt") {
            return 1000.0;
        }
        if ($unidad === "w" || $unidad === "watt" || $unidad === "vatio") {
            return 1.0;
        }
        // Auto: una instalación doméstica no supera 100 kW; si el máximo es
        // menor o igual a 100, casi con seguridad el dato está en kW.
        return $maximo > 0.0 && $maximo <= 100.0 ? 1000.0 : 1.0;
    }

    /**
     * @return array{0:?DateTimeImmutable, 1:?DateTimeImmutable}
     */
    private function rangoMediciones(): array
    {
        $fila = $this->conexion
            ->query(
                "SELECT MIN(Fecha_hora) AS inicio, MAX(Fecha_hora) AS fin
                 FROM Mediciones
                 WHERE Panel_solar > 0"
            )
            ->fetch();
        if (!$fila || $fila["inicio"] === null) {
            return [null, null];
        }
        return [
            new DateTimeImmutable(
                (string) $fila["inicio"],
                $this->zonaHoraria
            ),
            new DateTimeImmutable(
                (string) $fila["fin"],
                $this->zonaHoraria
            ),
        ];
    }

    private function flotante(mixed $valor): ?float
    {
        if ($valor === null || $valor === "" || !is_numeric($valor)) {
            return null;
        }
        return (float) $valor;
    }

    private function entero(mixed $valor): ?int
    {
        if ($valor === null || $valor === "" || !is_numeric($valor)) {
            return null;
        }
        return (int) $valor;
    }
}
