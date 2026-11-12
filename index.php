<?php

/**
 * Panel principal de PowerMate: predicción solar con IA y aprendizaje
 * continuo a partir del clima y las mediciones guardadas en MySQL.
 */

declare(strict_types=1);

['conexion' => $conexion, 'config' => $config] =
    require __DIR__ . "/config/bootstrap.php";

function h(mixed $valor): string
{
    return htmlspecialchars((string) $valor, ENT_QUOTES, "UTF-8");
}

function kwh(mixed $valor): string
{
    return number_format((float) $valor, 2, ",", ".");
}

function vatios(mixed $valor): string
{
    return number_format((float) $valor, 0, ",", ".");
}

function horaCorta(?string $hora): string
{
    if ($hora === null || $hora === "") {
        return "—";
    }
    return substr($hora, 0, 5);
}

function fechaLarga(?string $fecha): string
{
    if ($fecha === null || $fecha === "") {
        return "—";
    }
    try {
        return (new DateTimeImmutable($fecha))->format("d/m/Y H:i");
    } catch (Throwable $e) {
        return (string) $fecha;
    }
}

function etiquetaDia(string $fecha, DateTimeImmutable $hoy): string
{
    if ($fecha === $hoy->format("Y-m-d")) {
        return "Hoy";
    }
    if ($fecha === $hoy->modify("+1 day")->format("Y-m-d")) {
        return "Mañana";
    }
    $dias = ["dom", "lun", "mar", "mié", "jue", "vie", "sáb"];
    $objeto = new DateTimeImmutable($fecha);
    return $dias[(int) $objeto->format("w")] . " " . $objeto->format("d/m");
}

function rangoEntrenamiento(?string $detalle): string
{
    if ($detalle === null || $detalle === "") {
        return "—";
    }
    $datos = json_decode($detalle, true);
    if (!is_array($datos)) {
        return "—";
    }
    return (string) ($datos["data_range"] ?? "—");
}

$errorInicial = null;
$diarias = [];
$resumen = ["dias" => 0, "energia" => 0.0, "pico" => 0.0];
$ultima = null;
$aprendizajeResumen = [
    "mediciones_totales" => 0,
    "mediciones_nuevas" => 0,
    "minimas_para_reentrenar" => 48,
    "ultimo" => null,
    "historial" => [],
];
$totalClima = 0;
$climaRadiacion = 0;
$rangoClima = null;
$actualizado = null;

try {
    $repoEnergia = new RepositorioEnergia($conexion, $config);
    $repoClima = new RepositorioClima($conexion, $config);
    $ia = new SolarAI($config);
    $aprendizaje = new Aprendizaje(
        $conexion,
        $config,
        $repoClima,
        $ia
    );

    $zona = $repoClima->zonaHoraria();
    $hoy = new DateTimeImmutable("today", $zona);
    $manana = $hoy->modify("+1 day");

    $diarias = $repoEnergia->obtenerDiarias($hoy, 10);
    $resumen = $repoEnergia->resumenProximosDias($hoy);
    $ultima = $repoEnergia->ultimaPrediccion();
    $totalClima = $repoClima->contarRegistros();
    $climaRadiacion = $repoClima->contarConRadiacion();
    $rangoClima = $repoClima->rangoDisponible();
    $actualizado = $repoClima->ultimaActualizacion();
    $aprendizajeResumen = $aprendizaje->resumen();
} catch (Throwable $e) {
    $errorInicial = $e->getMessage();
    $zona = new DateTimeZone(
        (string) ($config["sitio"]["zona_horaria"] ?? "UTC")
    );
    $hoy = new DateTimeImmutable("today", $zona);
    $manana = $hoy->modify("+1 day");
}

$hoyFila = null;
$mananaFila = null;
foreach ($diarias as $fila) {
    if (($fila["Fecha"] ?? "") === $hoy->format("Y-m-d")) {
        $hoyFila = $fila;
    }
    if (($fila["Fecha"] ?? "") === $manana->format("Y-m-d")) {
        $mananaFila = $fila;
    }
}

$maxEnergia = 0.0;
foreach ($diarias as $fila) {
    $maxEnergia = max($maxEnergia, (float) ($fila["Energia_kwh"] ?? 0.0));
}
$maxEnergia = max($maxEnergia, 0.01);

$modelo = $ultima ?? [];
$ultimoEntrenamiento = $aprendizajeResumen["ultimo"] ?? null;
$r2 = $modelo["R2"] ?? ($ultimoEntrenamiento["R2"] ?? null);
$mae = $modelo["Mae_w"] ?? ($ultimoEntrenamiento["Mae_w"] ?? null);
$fuente = $modelo["Origen_modelo"]
    ?? ($ultimoEntrenamiento["Fuente"] ?? null);
$modeloId = $modelo["Modelo_id"]
    ?? ($ultimoEntrenamiento["Modelo_id"] ?? null);
$confianza = $modelo["Confianza_pct"] ?? null;
$incertidumbre = $modelo["Incertidumbre_w"] ?? null;

$medicionesNuevas = (int) ($aprendizajeResumen["mediciones_nuevas"] ?? 0);
$minimas = max(
    1,
    (int) ($aprendizajeResumen["minimas_para_reentrenar"] ?? 48)
);
$progreso = min(100.0, $medicionesNuevas / $minimas * 100.0);

$mensaje = isset($_GET["mensaje"]) && is_string($_GET["mensaje"])
    ? $_GET["mensaje"]
    : null;
$tipo = isset($_GET["tipo"]) && is_string($_GET["tipo"])
    ? $_GET["tipo"]
    : "ok";
if (!in_array($tipo, ["ok", "error", "aviso"], true)) {
    $tipo = "ok";
}

$sitio = $config["sitio"];
$sistema = $config["sistema"];

?>
<!DOCTYPE html>
<html lang="es">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <title>PowerMate · Predicción solar con IA</title>
    <link rel="stylesheet" href="assets/estilos.css">
</head>
<body>
<main>
    <header class="cabecera">
        <div>
            <h1>PowerMate · IA solar</h1>
            <div class="sub">
                <?= h($sitio["nombre"]) ?>
                (<?= h(number_format((float) $sitio["latitud"], 3, ",", ".")) ?>,
                <?= h(number_format((float) $sitio["longitud"], 3, ",", ".")) ?>)
                · <?= h(number_format((float) $sistema["kwp"], 2, ",", ".")) ?> kWp
                · inclinación <?= h(number_format((float) $sistema["inclinacion"], 0)) ?>°
                · azimut <?= h(number_format((float) $sistema["azimut"], 0)) ?>°
            </div>
        </div>
        <div class="badge">Open-Meteo + regresión lineal</div>
    </header>

    <?php if ($mensaje !== null): ?>
        <div class="flash <?= h($tipo) ?>"><?= h($mensaje) ?></div>
    <?php endif; ?>

    <?php if ($errorInicial !== null): ?>
        <div class="flash error">
            <strong>No se pudo cargar el panel:</strong>
            <?= h($errorInicial) ?><br>
            Ejecuta la migración con
            <code>/opt/lampp/bin/php config/migracion_ai.php</code>
            y vuelve a cargar la página.
        </div>
    <?php endif; ?>

    <div class="acciones">
        <form action="guardar_predicciones.php" method="post">
            <button type="submit" class="primario">
                Actualizar clima y predecir
            </button>
        </form>
        <form action="predecir_energia.php" method="post">
            <button type="submit" class="secundario">
                Generar predicción
            </button>
        </form>
        <form action="reentrenar_modelo.php" method="post">
            <button type="submit" class="peligro">
                Reentrenar IA ahora
            </button>
        </form>
    </div>

    <div class="cards">
        <div class="card">
            <span>Próximos días</span>
            <strong><?= h(kwh($resumen["energia"])) ?> kWh</strong>
            <small><?= h((int) $resumen["dias"]) ?> días previstos</small>
        </div>
        <div class="card">
            <span>Hoy</span>
            <strong><?= h(kwh($hoyFila["Energia_kwh"] ?? 0)) ?> kWh</strong>
            <small>día completo</small>
        </div>
        <div class="card">
            <span>Mañana</span>
            <strong><?= h(kwh($mananaFila["Energia_kwh"] ?? 0)) ?> kWh</strong>
            <small>día completo</small>
        </div>
        <div class="card">
            <span>Pico previsto</span>
            <strong><?= h(kwh((float) $resumen["pico"] / 1000.0)) ?> kW</strong>
            <small>máximo del periodo</small>
        </div>
        <div class="card">
            <span>Confianza</span>
            <strong><?= $confianza !== null ? h(number_format((float) $confianza, 1, ",", ".")) . "%" : "—" ?></strong>
            <small>R² del modelo</small>
        </div>
        <div class="card">
            <span>Mediciones nuevas</span>
            <strong><?= h($medicionesNuevas) ?></strong>
            <small>para reentrenar: <?= h($minimas) ?></small>
        </div>
    </div>

    <div class="dos-columnas">
        <section>
            <h2>Energía prevista por día</h2>
            <?php if ($diarias === []): ?>
                <div class="vacio">
                    Todavía no hay predicciones guardadas. Pulsa
                    <strong>Actualizar clima y predecir</strong> para
                    generarlas.
                </div>
            <?php else: ?>
                <div class="barras">
                    <?php foreach ($diarias as $fila):
                        $energia = (float) ($fila["Energia_kwh"] ?? 0.0);
                        $altura = max(3.0, $energia / $maxEnergia * 100.0);
                    ?>
                        <div class="barra"
                             title="<?= h($fila["Fecha"]) ?> · <?= h(kwh($energia)) ?> kWh">
                            <div class="valor"><?= h(kwh($energia)) ?></div>
                            <div class="relleno"
                                 style="height: <?= h(number_format($altura, 1, ".", "")) ?>%"></div>
                            <div class="etiqueta"><?= h(etiquetaDia((string) $fila["Fecha"], $hoy)) ?></div>
                        </div>
                    <?php endforeach; ?>
                </div>

                <h3>Detalle diario</h3>
                <table>
                    <thead>
                    <tr>
                        <th>Fecha</th>
                        <th class="num">kWh</th>
                        <th class="num">Pico kW</th>
                        <th>Hora pico</th>
                        <th class="num">GTI máx</th>
                        <th class="num">T máx</th>
                        <th class="num">Nubes</th>
                        <th class="num">Conf.</th>
                    </tr>
                    </thead>
                    <tbody>
                    <?php foreach ($diarias as $fila): ?>
                        <tr class="<?= ($fila["Fecha"] ?? "") === $hoy->format("Y-m-d") ? "hoy" : "" ?>">
                            <td><?= h(etiquetaDia((string) $fila["Fecha"], $hoy)) ?></td>
                            <td class="num"><?= h(kwh($fila["Energia_kwh"] ?? 0)) ?></td>
                            <td class="num"><?= h(kwh((float) ($fila["Potencia_pico_w"] ?? 0) / 1000.0)) ?></td>
                            <td><?= h(horaCorta($fila["Hora_pico"] ?? null)) ?></td>
                            <td class="num"><?= h(vatios($fila["Gti_max_wm2"] ?? 0)) ?></td>
                            <td class="num"><?= h(number_format((float) ($fila["Temperatura_max_c"] ?? 0), 1, ",", ".")) ?>°</td>
                            <td class="num"><?= h(number_format((float) ($fila["Nubosidad_media"] ?? 0), 0)) ?>%</td>
                            <td class="num"><?= $fila["Confianza_pct"] !== null ? h(number_format((float) $fila["Confianza_pct"], 0)) . "%" : "—" ?></td>
                        </tr>
                    <?php endforeach; ?>
                    </tbody>
                </table>
            <?php endif; ?>
        </section>

        <section>
            <h2>Modelo de IA</h2>
            <?php if ($fuente === null && $r2 === null): ?>
                <div class="vacio">
                    El modelo se entrenará automáticamente en la primera
                    predicción usando el clima almacenado.
                </div>
            <?php else: ?>
                <div class="metricas">
                    <div class="metrica">
                        <span>R²</span>
                        <strong><?= $r2 !== null ? h(number_format((float) $r2, 4, ",", ".")) : "—" ?></strong>
                    </div>
                    <div class="metrica">
                        <span>MAE</span>
                        <strong><?= $mae !== null ? h(vatios($mae)) . " W" : "—" ?></strong>
                    </div>
                    <div class="metrica">
                        <span>Confianza</span>
                        <strong><?= $confianza !== null ? h(number_format((float) $confianza, 1, ",", ".")) . "%" : "—" ?></strong>
                    </div>
                    <div class="metrica">
                        <span>Incertidumbre 95%</span>
                        <strong><?= $incertidumbre !== null ? "±" . h(vatios($incertidumbre)) . " W" : "—" ?></strong>
                    </div>
                </div>
                <h3>Origen</h3>
                <table>
                    <tbody>
                    <tr>
                        <th>Fuente</th>
                        <td><?= h($fuente ?? "—") ?></td>
                    </tr>
                    <tr>
                        <th>Modelo</th>
                        <td><code><?= h($modeloId ?? "—") ?></code></td>
                    </tr>
                    <tr>
                        <th>Datos</th>
                        <td><?= h(rangoEntrenamiento($ultimoEntrenamiento["Detalle"] ?? null)) ?></td>
                    </tr>
                    <tr>
                        <th>Último entrenamiento</th>
                        <td><?= h(fechaLarga($ultimoEntrenamiento["Fecha"] ?? null)) ?></td>
                    </tr>
                    </tbody>
                </table>
            <?php endif; ?>

            <h3>Datos meteorológicos</h3>
            <table>
                <tbody>
                <tr>
                    <th>Filas guardadas</th>
                    <td><?= h(number_format($totalClima, 0, ",", ".")) ?></td>
                </tr>
                <tr>
                    <th>Con radiación</th>
                    <td><?= h(number_format($climaRadiacion, 0, ",", ".")) ?></td>
                </tr>
                <tr>
                    <th>Rango</th>
                    <td>
                        <?= $rangoClima !== null
                            ? h(fechaLarga($rangoClima["inicio"])) . " → " . h(fechaLarga($rangoClima["fin"]))
                            : "—" ?>
                    </td>
                </tr>
                <tr>
                    <th>Última actualización</th>
                    <td><?= h(fechaLarga($actualizado)) ?></td>
                </tr>
                </tbody>
            </table>
            <?php if ($climaRadiacion === 0): ?>
                <div class="flash aviso" style="margin-top:12px">
                    Los datos guardados todavía no incluyen radiación solar.
                    Pulsa <strong>Actualizar clima y predecir</strong> para
                    descargar el pronóstico completo.
                </div>
            <?php endif; ?>
        </section>
    </div>

    <section>
        <h2>Aprendizaje continuo</h2>
        <p class="sub" style="margin-top:-4px">
            El modelo se recalibra con las mediciones reales de
            <code>Mediciones.Panel_solar</code> cuando hay suficientes datos
            nuevos. Mientras tanto, aprende del clima que la web va acumulando
            en <code>Predicciones_Meteorologicas</code>.
        </p>

        <div class="metricas" style="grid-template-columns:repeat(3,1fr)">
            <div class="metrica">
                <span>Mediciones totales</span>
                <strong><?= h(number_format((int) $aprendizajeResumen["mediciones_totales"], 0, ",", ".")) ?></strong>
            </div>
            <div class="metrica">
                <span>Nuevas desde el último entrenamiento</span>
                <strong><?= h($medicionesNuevas) ?></strong>
            </div>
            <div class="metrica">
                <span>Umbral de reentrenamiento</span>
                <strong><?= h($minimas) ?></strong>
            </div>
        </div>

        <div class="progreso" title="<?= h(number_format($progreso, 0)) ?>%">
            <span style="width: <?= h(number_format($progreso, 1, ".", "")) ?>%"></span>
        </div>
        <div class="sub">
            <?= $medicionesNuevas >= $minimas
                ? "Hay datos suficientes: el próximo reentrenamiento es automático."
                : "Faltan " . h(max(0, $minimas - $medicionesNuevas)) . " mediciones para el próximo reentrenamiento automático." ?>
        </div>

        <h3>Historial de entrenamientos</h3>
        <?php if (($aprendizajeResumen["historial"] ?? []) === []): ?>
            <div class="vacio">Aún no hay entrenamientos registrados.</div>
        <?php else: ?>
            <table>
                <thead>
                <tr>
                    <th>Fecha</th>
                    <th>Origen</th>
                    <th>Fuente</th>
                    <th class="num">Muestras</th>
                    <th class="num">Reales</th>
                    <th class="num">R²</th>
                    <th class="num">MAE</th>
                </tr>
                </thead>
                <tbody>
                <?php foreach ($aprendizajeResumen["historial"] as $entrenamiento): ?>
                    <tr>
                        <td><?= h(fechaLarga($entrenamiento["Fecha"] ?? null)) ?></td>
                        <td><?= h($entrenamiento["Origen"] ?? "—") ?></td>
                        <td><?= h($entrenamiento["Fuente"] ?? "—") ?></td>
                        <td class="num"><?= h($entrenamiento["Muestras_entrenamiento"] ?? "—") ?></td>
                        <td class="num"><?= h($entrenamiento["Muestras_reales"] ?? "—") ?></td>
                        <td class="num"><?= $entrenamiento["R2"] !== null ? h(number_format((float) $entrenamiento["R2"], 4, ",", ".")) : "—" ?></td>
                        <td class="num"><?= $entrenamiento["Mae_w"] !== null ? h(vatios($entrenamiento["Mae_w"])) . " W" : "—" ?></td>
                    </tr>
                <?php endforeach; ?>
                </tbody>
            </table>
        <?php endif; ?>
    </section>

    <p class="aviso-legal">
        La predicción es una estimación. La incertidumbre real depende de la
        calidad del pronóstico meteorológico, del estado de los paneles y de la
        calibración con datos de producción. La IA se reentrena automáticamente
        cuando se acumulan mediciones nuevas.
    </p>

    <footer>
        PowerMate · Predicción solar con IA ·
        <?= h($sitio["zona_horaria"]) ?> ·
        <a href="config/migracion_ai.php">Migración de base de datos</a>
    </footer>
</main>
</body>
</html>
