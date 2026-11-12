<?php

/**
 * Cliente de Open-Meteo para el pronóstico horario.
 *
 * Descarga todas las variables que necesita el modelo de IA (radiación,
 * nubosidad, viento, humedad, nieve…) además de las que ya usaba la web.
 */

declare(strict_types=1);

/**
 * Variables horarias solicitadas a Open-Meteo.
 *
 * La irradiancia sobre el plano inclinado (global_tilted_irradiance) la
 * calcula Open-Meteo a partir de "tilt" y "azimuth", que es mucho más preciso
 * que estimarla a mano.
 */
function variablesMeteorologicas(): array
{
    return [
        "temperature_2m",
        "relative_humidity_2m",
        "rain",
        "precipitation",
        "precipitation_probability",
        "cloud_cover",
        "wind_speed_10m",
        "shortwave_radiation",
        "direct_normal_irradiance",
        "diffuse_radiation",
        "global_tilted_irradiance",
        "snow_depth",
        "weather_code",
        "is_day",
    ];
}

/**
 * Descarga el pronóstico y devuelve los metadatos y las horas.
 *
 * @return array{zona_horaria:string, latitud:float, longitud:float,
 *               elevacion:?float, horas:array<string, array<int, mixed>>}
 */
function obtenerPronosticoMeteorologico(array $config = []): array
{
    $sitio = $config["sitio"] ?? [];
    $sistema = $config["sistema"] ?? [];
    $pronostico = $config["pronostico"] ?? [];

    $latitud = (float) ($sitio["latitud"] ?? -34.5833);
    $longitud = (float) ($sitio["longitud"] ?? -60.95);
    $zonaHoraria = (string) (
        $sitio["zona_horaria"] ?? "America/Argentina/Buenos_Aires"
    );
    $inclinacion = (float) ($sistema["inclinacion"] ?? 30.0);
    $azimut = (float) ($sistema["azimut"] ?? 0.0);
    $dias = (int) ($pronostico["dias"] ?? 7);

    if ($dias < 1 || $dias > 16) {
        throw new Exception("Los días de pronóstico deben estar entre 1 y 16.");
    }

    $parametros = [
        "latitude" => $latitud,
        "longitude" => $longitud,
        "hourly" => implode(",", variablesMeteorologicas()),
        "tilt" => $inclinacion,
        "azimuth" => $azimut,
        "forecast_days" => $dias,
        "timezone" => $zonaHoraria,
    ];
    $url = "https://api.open-meteo.com/v1/forecast?"
        . http_build_query($parametros, "", "&", PHP_QUERY_RFC3986);

    if (!function_exists("curl_init")) {
        throw new Exception("La extensión cURL no está habilitada en PHP.");
    }

    $curl = curl_init();
    if ($curl === false) {
        throw new Exception("No se pudo inicializar cURL.");
    }

    curl_setopt_array($curl, [
        CURLOPT_URL => $url,
        CURLOPT_RETURNTRANSFER => true,
        CURLOPT_TIMEOUT => 30,
        CURLOPT_CONNECTTIMEOUT => 10,
        CURLOPT_SSL_VERIFYPEER => true,
        CURLOPT_FOLLOWLOCATION => true,
    ]);

    $respuesta = curl_exec($curl);
    $error = curl_error($curl);
    $codigoHTTP = curl_getinfo($curl, CURLINFO_HTTP_CODE);
    curl_close($curl);

    if ($respuesta === false) {
        throw new Exception("Error de conexión con Open-Meteo: " . $error);
    }
    if ($codigoHTTP !== 200) {
        throw new Exception(
            "Open-Meteo devolvió el código HTTP: " . $codigoHTTP
        );
    }

    $datos = json_decode($respuesta, true);
    if (json_last_error() !== JSON_ERROR_NONE) {
        throw new Exception("Error al interpretar el JSON de Open-Meteo.");
    }
    if (isset($datos["error"]) && $datos["error"]) {
        throw new Exception(
            "Open-Meteo devolvió un error: "
            . ($datos["reason"] ?? "desconocido")
        );
    }

    $horas = $datos["hourly"] ?? null;
    if (!is_array($horas) || !isset($horas["time"])) {
        throw new Exception("La API no devolvió datos horarios.");
    }

    $cantidad = count($horas["time"]);
    foreach (variablesMeteorologicas() as $variable) {
        if (!isset($horas[$variable])) {
            // Algunas variables pueden faltar según el modelo meteorológico.
            $horas[$variable] = array_fill(0, $cantidad, null);
        }
    }

    return [
        "zona_horaria" => (string) ($datos["timezone"] ?? $zonaHoraria),
        "latitud" => (float) ($datos["latitude"] ?? $latitud),
        "longitud" => (float) ($datos["longitude"] ?? $longitud),
        "elevacion" => isset($datos["elevation"])
            ? (float) $datos["elevation"]
            : null,
        "horas" => $horas,
    ];
}

/**
 * Compatibilidad con el código anterior: devuelve solo el bloque horario.
 */
function obtenerDatosMeteorologicos(array $config = []): array
{
    $pronostico = obtenerPronosticoMeteorologico($config);
    return $pronostico["horas"];
}
