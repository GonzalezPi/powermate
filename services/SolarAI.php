<?php

/**
 * Puente entre PHP y el modelo de IA en Python.
 *
 * Ejecuta ``Model/bridge.py`` y le pasa un documento JSON por la entrada
 * estándar. El script devuelve otro JSON por la salida estándar. No se usa
 * ningún servicio externo ni se expone Python al navegador.
 */

declare(strict_types=1);

class SolarAI
{
    private array $config;

    public function __construct(array $config)
    {
        $this->config = $config;
    }

    /**
     * Predice potencia y energía a partir del clima almacenado.
     *
     * @param list<array<string, mixed>> $clima
     * @return array<string, mixed>
     */
    public function predecir(array $clima, ?bool $autoEntrenar = null): array
    {
        if ($clima === []) {
            throw new RuntimeException(
                "No hay datos meteorológicos con radiación para predecir. "
                . "Actualiza primero el pronóstico."
            );
        }

        $auto = $autoEntrenar
            ?? (bool) ($this->config["ia"]["auto_entrenar"] ?? true);

        return $this->ejecutar([
            "action" => "predict",
            "config" => $this->configPython(),
            "weather" => $clima,
            "auto_train" => $auto,
        ]);
    }

    /**
     * Reentrena el modelo con clima almacenado y, si existen, mediciones.
     *
     * @param list<array<string, mixed>> $clima
     * @param list<array<string, mixed>> $mediciones
     * @param array<string, mixed> $opciones
     * @return array<string, mixed>
     */
    public function entrenar(
        array $clima,
        array $mediciones = [],
        array $opciones = []
    ): array {
        $minimas = (int) (
            $opciones["min_real_samples"]
            ?? ($this->config["ia"]["min_muestras_reales"] ?? 100)
        );

        return $this->ejecutar([
            "action" => "train",
            "config" => $this->configPython(),
            "weather" => $clima,
            "measurements" => $mediciones,
            "min_real_samples" => $minimas,
            "verbose" => (bool) ($opciones["verbose"] ?? false),
        ]);
    }

    /**
     * Consulta el estado del modelo activo.
     *
     * @return array<string, mixed>
     */
    public function info(): array
    {
        return $this->ejecutar([
            "action" => "info",
            "config" => $this->configPython(),
        ]);
    }

    // ------------------------------------------------------------------
    // Ejecución del proceso
    // ------------------------------------------------------------------
    /**
     * @param array<string, mixed> $payload
     * @return array<string, mixed>
     */
    private function ejecutar(array $payload): array
    {
        $python = $this->rutaPython();
        $puente = (string) ($this->config["puente"] ?? "");
        if ($puente === "" || !is_file($puente)) {
            throw new RuntimeException(
                "No se encuentra el puente de IA en '$puente'."
            );
        }

        $comando = [$python, $puente];
        if (!empty($payload["verbose"])) {
            $comando[] = "--verbose";
        }

        $descriptores = [
            0 => ["pipe", "r"],
            1 => ["pipe", "w"],
            2 => ["pipe", "w"],
        ];

        $entorno = getenv();
        if (!is_array($entorno)) {
            $entorno = [];
        }
        // XAMPP exporta /opt/lampp/lib en LD_LIBRARY_PATH y esa libstdc++
        // es demasiado antigua para numpy/scikit-learn. El intérprete debe
        // usar las librerías del sistema.
        unset($entorno["LD_LIBRARY_PATH"], $entorno["LD_PRELOAD"]);
        $entorno["PYTHONUNBUFFERED"] = "1";
        $entorno["PYTHONIOENCODING"] = "utf-8";
        $entorno["PYTHONPATH"] = dirname($puente);

        $proceso = proc_open(
            $comando,
            $descriptores,
            $tuberias,
            dirname($puente),
            $entorno
        );
        if (!is_resource($proceso)) {
            throw new RuntimeException(
                "No se pudo iniciar el proceso de Python."
            );
        }

        $json = json_encode(
            $payload,
            JSON_UNESCAPED_UNICODE
            | JSON_UNESCAPED_SLASHES
            | JSON_THROW_ON_ERROR
        );
        fwrite($tuberias[0], $json);
        fclose($tuberias[0]);

        $tiempoMaximo = (int) ($this->config["ia"]["tiempo_maximo"] ?? 300);
        $inicio = microtime(true);
        $salida = "";
        $errores = "";
        $expirado = false;

        stream_set_blocking($tuberias[1], false);
        stream_set_blocking($tuberias[2], false);

        while (true) {
            $salida .= (string) stream_get_contents($tuberias[1]);
            $errores .= (string) stream_get_contents($tuberias[2]);

            $estado = proc_get_status($proceso);
            if (!$estado["running"]) {
                break;
            }
            if ((microtime(true) - $inicio) > $tiempoMaximo) {
                $expirado = true;
                proc_terminate($proceso, 9);
                break;
            }
            usleep(50000);
        }

        $salida .= (string) stream_get_contents($tuberias[1]);
        $errores .= (string) stream_get_contents($tuberias[2]);

        foreach ($tuberias as $tuberia) {
            if (is_resource($tuberia)) {
                fclose($tuberia);
            }
        }
        proc_close($proceso);

        if ($errores !== "") {
            error_log("[PowerMate IA] " . trim($errores));
        }

        if ($expirado) {
            throw new RuntimeException(
                "La IA superó el tiempo máximo de $tiempoMaximo segundos."
            );
        }

        $salida = trim($salida);
        if ($salida === "") {
            throw new RuntimeException(
                "La IA no devolvió respuesta. Detalle: "
                . ($errores !== "" ? trim($errores) : "sin detalles")
            );
        }

        $resultado = json_decode($salida, true);
        if (!is_array($resultado)) {
            throw new RuntimeException(
                "La respuesta de la IA no es un JSON válido: "
                . substr($salida, 0, 500)
            );
        }

        if (empty($resultado["ok"])) {
            throw new RuntimeException(
                "Error de la IA: "
                . ($resultado["error"] ?? "desconocido")
            );
        }

        return $resultado;
    }

    private function rutaPython(): string
    {
        $configurada = trim((string) ($this->config["python"] ?? ""));
        if ($configurada !== "") {
            if (!is_executable($configurada)) {
                throw new RuntimeException(
                    "El intérprete de Python configurado no es ejecutable: "
                    . "'$configurada'. Crea el entorno con "
                    . "'python3 -m venv Model/.venv' e instala "
                    . "Model/requirements.txt."
                );
            }
            return $configurada;
        }

        foreach (["/usr/bin/python3", "/usr/local/bin/python3"] as $ruta) {
            if (is_executable($ruta)) {
                return $ruta;
            }
        }
        return "python3";
    }

    /**
     * Traduce la configuración en español a las claves del modelo Python.
     *
     * @return array<string, array<string, mixed>>
     */
    private function configPython(): array
    {
        $sitio = $this->config["sitio"] ?? [];
        $sistema = $this->config["sistema"] ?? [];
        $entrenamiento = $this->config["entrenamiento"] ?? [];

        $acKwp = $sistema["ac_kwp"] ?? null;

        return [
            "site" => [
                "name" => (string) ($sitio["nombre"] ?? "Instalación"),
                "latitude" => (float) ($sitio["latitud"] ?? 0.0),
                "longitude" => (float) ($sitio["longitud"] ?? 0.0),
                "timezone" => (string) (
                    $sitio["zona_horaria"] ?? "UTC"
                ),
            ],
            "system" => [
                "kwp" => (float) ($sistema["kwp"] ?? 3.0),
                "tilt" => (float) ($sistema["inclinacion"] ?? 30.0),
                "azimuth" => (float) ($sistema["azimut"] ?? 0.0),
                "ac_kwp" => $acKwp === null ? null : (float) $acKwp,
                "inverter_efficiency" => (float) (
                    $sistema["eficiencia_inversor"] ?? 0.96
                ),
                "temp_coefficient" => (float) (
                    $sistema["coeficiente_temperatura"] ?? -0.0035
                ),
                "noct" => (float) ($sistema["noct"] ?? 45.0),
                "soiling_loss" => (float) (
                    $sistema["perdida_suciedad"] ?? 0.03
                ),
                "wiring_loss" => (float) (
                    $sistema["perdida_cableado"] ?? 0.02
                ),
                "mismatch_loss" => (float) (
                    $sistema["perdida_desacoplamiento"] ?? 0.02
                ),
                "snow_loss" => (bool) (
                    $sistema["perdida_nieve"] ?? true
                ),
                "snow_zero_cm" => (float) (
                    $sistema["nieve_cero_cm"] ?? 2.0
                ),
            ],
            "training" => [
                "years" => (float) ($entrenamiento["anios"] ?? 2.0),
                "test_fraction" => (float) (
                    $entrenamiento["fraccion_prueba"] ?? 0.2
                ),
                "random_state" => (int) (
                    $entrenamiento["semilla"] ?? 42
                ),
                "domain_randomization" => (bool) (
                    $entrenamiento["aleatorizacion"] ?? true
                ),
                "poly" => (bool) ($entrenamiento["poly"] ?? false),
            ],
        ];
    }
}
