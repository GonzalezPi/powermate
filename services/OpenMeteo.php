
<?php

function obtenerDatosMeteorologicos() {

    // URL de Open-Meteo: pronostico de 3 dias

    $url = "https://api.open-meteo.com/v1/forecast?"
         . "latitude=-34.585"
         . "&longitude=-60.95889"
         . "&hourly=temperature_2m,rain,precipitation_probability"
         . "&forecast_days=3"
         . "&timezone=America%2FArgentina%2FBuenos_Aires";


    // Comprobar que la extension cURL este instalada

    if (!function_exists("curl_init")) {
        throw new Exception("La extension cURL no esta habilitada en PHP.");
    }


    // Inicializar cURL

    $curl = curl_init();

    if ($curl === false) {
        throw new Exception("No se pudo inicializar cURL.");
    }


    // Configurar la consulta

    curl_setopt_array($curl, array(
        CURLOPT_URL => $url,
        CURLOPT_RETURNTRANSFER => true,
        CURLOPT_TIMEOUT => 30,
        CURLOPT_CONNECTTIMEOUT => 10,
        CURLOPT_SSL_VERIFYPEER => true,
        CURLOPT_FOLLOWLOCATION => true
    ));


    // Ejecutar la consulta

    $respuesta = curl_exec($curl);


    // Obtener informacion del resultado

    $error = curl_error($curl);
    $codigoHTTP = curl_getinfo($curl, CURLINFO_HTTP_CODE);


    // Cerrar cURL

    curl_close($curl);


    // Comprobar errores de conexion

    if ($respuesta === false) {
        throw new Exception(
            "Error de conexion con Open-Meteo: " . $error
        );
    }


    // Comprobar respuesta HTTP

    if ($codigoHTTP != 200) {
        throw new Exception(
            "Open-Meteo devolvio el codigo HTTP: " . $codigoHTTP
        );
    }


    // Convertir la respuesta JSON en un array

    $datos = json_decode($respuesta, true);


    // Comprobar errores del JSON

    if (json_last_error() !== JSON_ERROR_NONE) {
        throw new Exception(
            "Error al interpretar el JSON de Open-Meteo."
        );
    }


    // Comprobar que existan los datos horarios

    if (
        !isset($datos["hourly"]["time"]) ||
        !isset($datos["hourly"]["temperature_2m"]) ||
        !isset($datos["hourly"]["rain"]) ||
        !isset($datos["hourly"]["precipitation_probability"])
    ) {
        throw new Exception(
            "La API no devolvio todos los datos esperados."
        );
    }


    // Devolver los datos meteorologicos

    return $datos["hourly"];

}

?>
