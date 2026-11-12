<?php

/**
 * Arranque común de la aplicación: conexión, configuración y servicios.
 *
 * Uso:
 *     ['conexion' => $conexion, 'config' => $config] =
 *         require __DIR__ . '/config/bootstrap.php';
 */

declare(strict_types=1);

// Evita que json_encode escriba decimales con 100 cifras (XAMPP).
ini_set("serialize_precision", "-1");

require_once __DIR__ . "/database.php";

$config = require __DIR__ . "/ai.php";

require_once __DIR__ . "/../services/OpenMeteo.php";
require_once __DIR__ . "/../services/RepositorioClima.php";
require_once __DIR__ . "/../services/RepositorioEnergia.php";
require_once __DIR__ . "/../services/SolarAI.php";
require_once __DIR__ . "/../services/Aprendizaje.php";
require_once __DIR__ . "/../services/Prediccion.php";
require_once __DIR__ . "/../services/Respuesta.php";

return [
    "conexion" => $conexion,
    "config" => $config,
];
