<?php

/**
 * Ejemplo de configuración de la base de datos.
 *
 * Copia este archivo como config/database.php y ajusta los valores:
 *
 *     cp config/database.example.php config/database.php
 *
 * config/database.php está excluido de Git para no publicar credenciales.
 */

$host = "localhost";
$dbname = "sistema_monitoreo";
$user = "TU_USUARIO";
$password = "TU_CONTRASENA";

$conexion = new PDO(
    "mysql:host=" . $host . ";dbname=" . $dbname . ";charset=utf8",
    $user,
    $password
);

$conexion->setAttribute(
    PDO::ATTR_ERRMODE,
    PDO::ERRMODE_EXCEPTION
);
