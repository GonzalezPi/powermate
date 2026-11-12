<?php

/**
 * Utilidades de respuesta HTTP para los endpoints de la aplicación.
 */

declare(strict_types=1);

function redirigirConMensaje(
    string $mensaje,
    string $tipo = "ok",
    string $pagina = "index.php"
): never {
    $parametros = http_build_query([
        "mensaje" => $mensaje,
        "tipo" => $tipo,
    ]);
    $separador = str_contains($pagina, "?") ? "&" : "?";
    header("Location: " . $pagina . $separador . $parametros, true, 303);
    exit;
}

function responderJson(array $datos, int $codigo = 200): never
{
    http_response_code($codigo);
    header("Content-Type: application/json; charset=utf-8");
    echo json_encode(
        $datos,
        JSON_UNESCAPED_UNICODE | JSON_UNESCAPED_SLASHES
    );
    exit;
}

function quiereJson(): bool
{
    $formato = strtolower((string) ($_GET["formato"] ?? ""));
    return $formato === "json"
        || str_contains(
            strtolower((string) ($_SERVER["HTTP_ACCEPT"] ?? "")),
            "application/json"
        );
}
