
<?php

// 1. Incluir conexión a MySQL y servicio Open-Meteo

require_once __DIR__ . "/config/database.php";
require_once __DIR__ . "/services/OpenMeteo.php";

try {

    // 2. Obtener datos de Open-Meteo

    $datos = obtenerDatosMeteorologicos();

    $horas = $datos["time"];
    $temperaturas = $datos["temperature_2m"];
    $lluvias = $datos["rain"];
    $probabilidades = $datos["precipitation_probability"];

    // 3. Verificar cantidad de datos

    $cantidad = count($horas);

    if (
        count($temperaturas) !== $cantidad ||
        count($lluvias) !== $cantidad ||
        count($probabilidades) !== $cantidad
    ) {
        throw new Exception("Los datos recibidos no tienen la misma cantidad de registros.");
    }

    // 4. Preparar consulta SQL

    $sql = "INSERT INTO Predicciones_Meteorologicas
            (Fecha_hora, Temperatura, Precipitaciones, Consumo)
            VALUES (:fecha, :temperatura, :precipitaciones, :consumo)";

    $consulta = $conexion->prepare($sql);

    // 5. Iniciar transacción

    $conexion->beginTransaction();

    $insertados = 0;

    // 6. Recorrer las 72 horas e insertar los datos

    for ($i = 0; $i < $cantidad; $i++) {

        if (
            $temperaturas[$i] === null ||
            $lluvias[$i] === null
        ) {
            continue;
        }

        $consulta->execute([

            ":fecha" => $horas[$i],

            ":temperatura" => $temperaturas[$i],

            ":precipitaciones" => $lluvias[$i],

            // El consumo lo calcularemos más adelante

            ":consumo" => 0

        ]);

        $insertados++;

    }

    // 7. Confirmar los registros

    $conexion->commit();

    // 8. Mostrar resultado

    echo "<h2>¡Predicciones guardadas correctamente!</h2>";

    echo "<p>Horas recibidas desde la API: " . $cantidad . "</p>";

    echo "<p>Registros insertados en MySQL: " . $insertados . "</p>";

} catch (Throwable $e) {

    // Deshacer inserciones si ocurre algún error

    if (isset($conexion) && $conexion->inTransaction()) {
        $conexion->rollBack();
    }

    echo "<h2>Error al guardar las predicciones</h2>";

    echo "<p>" . htmlspecialchars($e->getMessage()) . "</p>";

}

?>
