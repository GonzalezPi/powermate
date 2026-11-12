<?php

/**
 * Configuración de la integración con la IA solar.
 *
 * Los valores de "sitio" y "sistema" deben coincidir con la instalación real:
 * el modelo se entrena y se guarda por combinación de coordenadas, potencia,
 * inclinación, azimut y potencia del inversor.
 */

declare(strict_types=1);

$configuracion = [
    "sitio" => [
        "nombre" => "Junín",
        "latitud" => -34.5833,
        "longitud" => -60.95,
        "zona_horaria" => "America/Argentina/Buenos_Aires",
    ],

    "sistema" => [
        "kwp" => 3.0,
        "inclinacion" => 30.0,
        "azimut" => 0.0,
        "ac_kwp" => 3.0,
        "eficiencia_inversor" => 0.96,
        "coeficiente_temperatura" => -0.0035,
        "noct" => 45.0,
        "perdida_suciedad" => 0.03,
        "perdida_cableado" => 0.02,
        "perdida_desacoplamiento" => 0.02,
        "perdida_nieve" => true,
        "nieve_cero_cm" => 2.0,
    ],

    "entrenamiento" => [
        "anios" => 2.0,
        "fraccion_prueba" => 0.2,
        "semilla" => 42,
        "aleatorizacion" => true,
        "poly" => false,
    ],

    // Ruta del intérprete de Python y del puente JSON.
    "python" => __DIR__ . "/../Model/.venv/bin/python",
    "puente" => __DIR__ . "/../Model/bridge.py",

    // Pronóstico que se pide a Open-Meteo y que luego se guarda en MySQL.
    "pronostico" => [
        "dias" => 7,
        "actualizar_al_predecir" => false,
        "predecir_tras_actualizar" => true,
    ],

    // Parámetros del puente IA.
    "ia" => [
        "tiempo_maximo" => 300,
        "auto_entrenar" => true,
        "auto_reentrenar" => true,
        // Mediciones nuevas necesarias para lanzar un reentrenamiento.
        "min_nuevas_mediciones" => 48,
        // Horas diurnas reales necesarias para calibrar con producción.
        "min_muestras_reales" => 100,
    ],

    // Cómo interpretar la columna Mediciones.Panel_solar.
    // "auto": valores <= 100 se consideran kW; el resto, W.
    "mediciones" => [
        "unidad" => "auto",
        "agregacion" => "sum",
    ],
];

// Permite sobrescribir la configuración sin tocar el archivo versionado.
$archivoLocal = __DIR__ . "/ai.local.php";
if (is_file($archivoLocal)) {
    $local = require $archivoLocal;
    if (is_array($local)) {
        $configuracion = array_replace_recursive($configuracion, $local);
    }
}

return $configuracion;
