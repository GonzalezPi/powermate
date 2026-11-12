
<?php

$host = "localhost";
$dbname = "sistema_monitoreo";
$user = "root";
$password = "";

$conexion = new PDO(
    "mysql:host=" . $host . ";dbname=" . $dbname . ";charset=utf8",
    $user,
    $password
);

$conexion->setAttribute(
    PDO::ATTR_ERRMODE,
    PDO::ERRMODE_EXCEPTION
);

?>
