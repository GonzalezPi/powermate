

<?php

// Mostrar todos los errores

error_reporting(E_ALL);

// Mostrar errores en pantalla

ini_set("display_errors", "1");
ini_set("display_startup_errors", "1");

// Activar registro de errores

ini_set("log_errors", "1");

// Archivo donde se guardaran los errores

ini_set("error_log", __DIR__ . "/php_errors.log");


// Convertir advertencias y avisos en excepciones

function manejarErrorPHP($nivel, $mensaje, $archivo, $linea) {

    if (!(error_reporting() & $nivel)) {
        return false;
    }

    throw new ErrorException(
        $mensaje,
        0,
        $nivel,
        $archivo,
        $linea
    );

}

set_error_handler("manejarErrorPHP");



try {

    // Incluir conexion y servicio meteorologico

    require_once __DIR__ . "/config/database.php";
    require_once __DIR__ . "/services/OpenMeteo.php";


    // Obtener datos de Open-Meteo

    $datos = obtenerDatosMeteorologicos();

    $horas = $datos["time"];
    $temperaturas = $datos["temperature_2m"];
    $lluvias = $datos["rain"];
    $probabilidades = $datos["precipitation_probability"];


    // Verificar cantidad de datos

    $cantidad = count($horas);

    if (
        count($temperaturas) != $cantidad ||
        count($lluvias) != $cantidad ||
        count($probabilidades) != $cantidad
    ) {

        throw new Exception(
            "Los datos recibidos no tienen la misma cantidad de registros."
        );

    }


    // Preparar consulta SQL

    $sql = "INSERT INTO Predicciones_Meteorologicas
            (Fecha_hora, Temperatura, Precipitaciones, Consumo)
            VALUES (:fecha, :temperatura, :precipitaciones, :consumo)";

    $consulta = $conexion->prepare($sql);


    // Iniciar transaccion

    $conexion->beginTransaction();

    $insertados = 0;


    // Recorrer los datos horarios

    for ($i = 0; $i < $cantidad; $i++) {

        // Saltar registros sin temperatura o lluvia

        if (
            $temperaturas[$i] === null ||
            $lluvias[$i] === null
        ) {
            continue;
        }


        // Insertar datos en MySQL

        $consulta->execute(array(

            ":fecha" => $horas[$i],

            ":temperatura" => $temperaturas[$i],

            ":precipitaciones" => $lluvias[$i],

            // El consumo se calculara mas adelante

            ":consumo" => 0

        ));

        $insertados++;

    }


    // Confirmar la transaccion

    $conexion->commit();


    // Mostrar resultado

    echo "<h2>Predicciones guardadas correctamente</h2>";

    echo "<p>Horas recibidas desde la API: "
         . $cantidad . "</p>";

    echo "<p>Registros insertados en MySQL: "
         . $insertados . "</p>";


} catch (Exception $e) {


    // Deshacer cambios si existe una transaccion activa

    if (
        isset($conexion) &&
        $conexion->inTransaction()
    ) {

        $conexion->rollBack();

    }


    // Registrar el error en el archivo de log

    error_log(
        $e->getMessage() .
        " | Archivo: " . $e->getFile() .
        " | Linea: " . $e->getLine()
    );


    // Mostrar el error en pantalla

    echo "<h2>Error al guardar las predicciones</h2>";

    echo "<p><strong>Mensaje:</strong> "
         . htmlspecialchars(
             $e->getMessage(),
             ENT_QUOTES,
             "UTF-8"
         )
         . "</p>";

    echo "<p><strong>Archivo:</strong> "
         . htmlspecialchars(
             $e->getFile(),
             ENT_QUOTES,
             "UTF-8"
         )
         . "</p>";

    echo "<p><strong>Linea:</strong> "
         . $e->getLine()
         . "</p>";

}

?>
