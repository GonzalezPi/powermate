
<?php

function obtenerDatosMeteorologicos() {

    // URL de Open-Meteo: pronóstico de 3 días

    $url = "https://api.open-meteo.com/v1/forecast?"
         . "latitude=-34.585"
         . "&longitude=-60.95889"
         . "&hourly=temperature_2m,rain,precipitation_probability"
         . "&forecast_days=3"
         . "&timezone=America%2FArgentina%2FBuenos_Aires";

    // Inicializar cURL

    $curl = curl_init();

    curl_setopt_array($curl, [
        CURLOPT_URL => $url,
        CURLOPT_RETURNTRANSFER => true,
        CURLOPT_TIMEOUT => 30,
        CURLOPT_CONNECTTIMEOUT => 10,
        CURLOPT_SSL_VERIFYPEER => true,
        CURLOPT_FOLLOWLOCATION => true
    ]);

    // Consultar API

    $respuesta = curl_exec($curl);

    // Obtener información de la consulta

    $error = curl_error($curl);
    $codigoHTTP = curl_getinfo($curl, CURLINFO_HTTP_CODE);

    curl_close($curl);

    // Comprobar errores de conexión

    if ($respuesta === false) {
        throw new Exception("Error de conexión con Open-Meteo: " . $error);
    }

    // Comprobar respuesta HTTP

    if ($codigoHTTP !== 200) {
        throw new Exception("Open-Meteo devolvió el código HTTP: " . $codigoHTTP);
    }

    // Convertir respuesta JSON

    $datos = json_decode($respuesta, true);

    if (json_last_error() !== JSON_ERROR_NONE) {
        throw new Exception("Error al interpretar el JSON de Open-Meteo.");
    }

    // Comprobar que existan los datos horarios

    if (
        !isset($datos["hourly"]["time"]) ||
        !isset($datos["hourly"]["temperature_2m"]) ||
        !isset($datos["hourly"]["rain"]) ||
        !isset($datos["hourly"]["precipitation_probability"])
    ) {
        throw new Exception("La API no devolvió todos los datos esperados.");
    }

    // Devolver datos meteorológicos

    return $datos["hourly"];

}
