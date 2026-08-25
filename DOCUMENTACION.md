# SISTEMA DE PRONOSTICOS DEPORTIVOS (Over/Under) — DOCUMENTO DE REPLICA MLB → NBA

> Documento de transferencia generado el 2026-08-22 desde el sistema MLB en produccion
> (repo `raifreitas/mlb-predictor`). Objetivo: replicar todo el sistema para el mercado
> Over/Under de la NBA.

---

## 1. VISION GENERAL

Pipeline 100% serverless en GitHub Actions (plan gratuito) que cada dia:
**carga resultados -> reentrena -> decide picks en la ventana pre-juego -> publica web ->
verifica resultados -> notifica por Telegram.**

Sistema MLB actual en produccion:

| Componente | Valor |
|---|---|
| Repo | `raifreitas/mlb-predictor` (publico, rama `main`) |
| Local | `C:\Users\raifj\source\repos\PruebaPronosticos` |
| Web | `https://raifreitas.github.io/mlb-predictor/` (GitHub Pages desde `/web`) |
| BD | `data/mlb.db` (SQLite, ~31.5k partidos desde 2015) **versionada en git** — la BD es el estado del sistema |
| Resultado actual | ~16 picks/mes, ~90% acierto reciente (muestra chica), backtest honesto ~55% |

Principio de diseno clave: **todo corre en la nube gratis**; la PC local solo sirve para desarrollar.

---

## 2. ESTRUCTURA DEL REPO (lo relevante)

```
├── .github/workflows/
│   ├── etl-mlb.yml          # ETL diario (5 crons de schedule)
│   ├── runner-mlb.yml       # Runner cada 5 min (crea picks + web + Telegram + Pages)
│   ├── healthcheck-mlb.yml  # Red de seguridad (re-dispara runner; abre issue si PAT muerto)
│   └── pages-manual.yml     # Deploy Pages manual (workflow_dispatch)
├── data/
│   ├── mlb.db               # BD SQLite completa (versionada en git)
│   └── horarios.json        # Estado del planificador (pendiente/ejecutado por partido/dia)
├── web/
│   ├── index.html           # Frontend estatico oscuro (lee data.json con fetch)
│   ├── data.json            # Generado por generar_web.py (NO editar a mano)
│   └── favicon.svg          # Logo de la pestania (SVG hecho a mano)
├── PruebaPronosticos/
│   ├── src/EtlPython/       # ETL en Python (el C# de src/ETL es legacy)
│   │   ├── etl_main.py          # Motor: `python etl_main.py FECHA FECHA` (resultados+jugadores+clima)
│   │   │                        #         y `python etl_main.py --solo-odds FECHA FECHA` (snapshot cuotas)
│   │   ├── mlb_data_fetcher.py  # MLB StatsAPI (gratis, sin key)
│   │   ├── odds_fetcher.py      # The Odds API (snapshots; plan free NO tiene historico -> captura ACTUAL)
│   │   ├── weather_service.py   # OpenWeather (temperatura/viento por estadio; feature del modelo)
│   │   ├── estadio_catalog.py   # Coordenadas de los parques
│   │   ├── respaldo_matinal.py  # Devuelve FALTA=0/1: ya cargo ayer? (lo usa el runner)
│   │   └── config.py, game_repository.py
│   ├── src/Modeling/
│   │   ├── db_utils.py              # Capa BD dual: SQL Server local / SQLite nube (env MLB_SQLITE=1).
│   │   │                            #   RAIZ, RUTA_SQLITE, conexion(), leer_sql(), ejecutar(), usar_sqlite()
│   │   ├── entrenar_modelo.py       # XGBoost totales: features, split 80/20 cronologico,
│   │   │                            #   RandomizedSearchCV+TimeSeriesSplit, calibracion isotonica sobre test
│   │   ├── predecir_hoy.py          # MOTOR de decision: constantes, ensemble, filtros defensivos,
│   │   │                            #   decidir_jugada() (importado por recomendar_apuestas)
│   │   ├── recomendar_apuestas.py   # Produccion O/U: odds reales (moda entre casas) + abridores probables
│   │   │                            #   + decidir + INSERT Predicciones/Evaluaciones + pares_ya_ejecutados()
│   │   ├── planificador.py          # Orquestador por partido: ventana pre-juego, estado en horarios.json
│   │   ├── verificar_predicciones.py# Resuelve PENDIENTE -> GANADA/PERDIDA/PUSH (+regla NO_VALIDA anti-trampa)
│   │   ├── generar_web.py           # BD -> web/data.json (si el contenido es identico NO reescribe)
│   │   ├── notificar_picks.py       # Telegram (anti-duplicados via tabla Notificaciones)
│   │   └── backtest_*/analisis_clv*/experimento_*  # Investigacion y validaciones
│   ├── models/*.pkl           # modelo_mlb_totales, calibracion_totales, transformadores_totales,
│   │                          #   columnas_totales (+ *_ml moneyline y regresion, secundarios)
│   ├── sql/                   # DDL original SQL Server (referencia historica)
│   └── scripts/, logs/        # Rutinas Windows locales (legacy; en la nube no se usan)
├── migracion/exportar_a_sqlite.py  # Migracion inicial SQL Server -> SQLite
└── requirements.txt          # pandas, scikit-learn, xgboost, joblib, requests, pyodbc...
```

### Tablas de la BD (SQLite `data/mlb.db`)
| Tabla | Contenido |
|---|---|
| `GameLog` | Un registro por partido finalizado (~31.5k, desde 2015). El entrenamiento usa SOLO filas con temperatura |
| `PitcherGameLog` | Pitcheos por jugador/partido (~290k) -> fatiga bullpen, ERAs |
| `PitcherMano`, `TeamOPS_Handedness` | Splits LHP/RHP para matchup |
| `HistoricalOdds`, `LineaSnapshots`, `LineaSnapshotsML` | Snapshots de cuotas Totals y Moneyline (varios por dia) |
| `ParkFactors` | Factores de parque |
| `Predicciones` | Los PICKS reales O/U (Estado: PENDIENTE/GANADA/PERDIDA/PUSH/NO_VALIDA, CreadoUtc, Cuota, Unidades...) |
| `PrediccionesML` | Picks moneyline (secundario) |
| `Evaluaciones` | TODO partido evaluado: APOSTAR o NO APOSTAR + motivo (alimenta "descartados" en la web) |
| `Notificaciones` | IdPick ya avisados por Telegram (anti-duplicados) |
| vista `vwFatigaBullpen3d` | Ventana movil 3 dias de pitcheos del bullpen (la crea db_utils si falta) |

---

## 3. LOS CRONS (⚠️ LO CRITICO — hay DOS capas)

### Capa 1: GitHub Actions (`etl-mlb.yml`) — 5 horarios UTC
| Cron (UTC) | Que hace |
|---|---|
| `30 6 * * *` | **Matinal completo**: `etl_main.py AYER AYER` (resultados finales) + `etl_main HOY HOY` (preparar hoy) + `--solo-odds HOY` + `verificar_predicciones.py` + `entrenar_modelo.py` |
| `30 14 * * *` | Solo `--solo-odds HOY` + verificar (tarde) |
| `0 18 * * *`  | Solo `--solo-odds HOY` + verificar |
| `0 21 * * *`  | Solo `--solo-odds HOY` + verificar (linea nocturna) |
| `0 23 * * *`  | Solo `--solo-odds HOY` + verificar (cierre) |

⚠️ GitHub Actions se RETRASA (el cron de 06:30 suele arrancar ~07:00-07:06). Es normal.
⚠️ **GitHub DESACTIVA los schedules tras 60 dias sin commits en el repo** (temporada muerta).
Llega un email; se reactiva con un clic en la pestana Actions. En offseason no hay commits
(ni picks), asi que esto PASARA — planear un mini-commit mensual o reactivar a mano en febrero.

### Capa 2: cron-job.org (externo, gratis) -> dispara `runner-mlb.yml` cada 5 minutos
- POST a `https://api.github.com/repos/<USUARIO>/<REPO>/actions/workflows/runner-mlb.yml/dispatches`
- Header: `Authorization: Bearer <PAT>` ; body: `{"ref":"main"}`
- El PAT clasico (nombre `cron-runner`) esta guardado como secret `DISPATCH_PAT`
- ⚠️ **Si el PAT expira, TODO el sistema muere en silencio.** El healthcheck abre un issue
  si falla el re-despacho, pero SOLO en dias con juegos pendientes. Revisar vencimiento antes
  de cada temporada.
- `runner-mlb.yml` tiene ademas su propio schedule `*/5 * * * *` como respaldo

### Pasos internos de CADA tick del runner (cada 5 min):
1. **Respaldo matinal** (solo >= 06:45 UTC): si `respaldo_matinal.py AYER` devuelve FALTA!=0
   (el ETL de las 06:30 no corrio), el runner asume: carga de ayer + carga de hoy +
   snapshot + verificar + **reentrenar**
2. **Planificador**: `python planificador.py --procesar --ventana-min 30 --horizonte-max-min 120`
   -> evalua CADA PARTIDO una sola vez dentro de su ventana pre-juego
3. **Notificar Telegram**: `notificar_picks.py`
4. **generar_web.py**
5. Commit+push SOLO si hubo cambios (`changed=1`) -> ese commit dispara el deploy de Pages
   (`upload-pages-artifact` + `deploy-pages` con `if: env.changed == '1'`)

### Healthcheck (`healthcheck-mlb.yml`, cada 4h en hora :20)
Si hay partidos `pendiente` hoy en horarios.json y `web/data.json` lleva >4h sin commit ->
re-dispara runner via DISPATCH_PAT. Si el re-despacho falla (PAT expirado/borrado) -> abre un
issue con instrucciones.

### Deploy manual (`pages-manual.yml`)
workflow_dispatch que hace checkout + upload-pages-artifact(web) + deploy-pages.
Util para publicar cambios de la web SIN esperar al siguiente commit del runner
(así se desplego el favicon).

---

## 4. SECRETOS Y VARIABLES

Secrets del repo (Settings -> Secrets and variables -> Actions):
`THE_ODDS_API_KEY` · `OPENWEATHER_API_KEY` · `DISPATCH_PAT` · `TELEGRAM_BOT_TOKEN` · `TELEGRAM_CHAT_ID`

Todos los workflows definen env `MLB_SQLITE=1` (fuerza SQLite; sin eso intentaria SQL Server local).

Telegram: bot creado con @BotFather; chat_id propio del dueno; `notificar_picks.py` avisa picks
PENDIENTE con CreadoUtc menor a 12 min que no esten en la tabla Notificaciones. Si faltan secrets,
no rompe nada (solo imprime aviso y sale 0). El bot solo ENVIA; nunca escucha ni responde;
es gratis para siempre.

---

## 5. FLUJO DE VIDA DE UN PICK

1. T-60min: el planificador ve el juego en horizonte -> marca `pendiente` en `data/horarios.json`
2. Dentro de la ventana (T-30): `recomendar_apuestas.py --fecha X --ventana-min 30`:
   - Odds reales de The Odds API (moda entre casas; fallback 1.91 si falta)
   - Abridores probables de StatsAPI
   - Proyeccion Expected_Runs_Ajustada (ensemble + ajustes dinamicos clip ±0.60)
   - `decidir_jugada()` -> **APOSTAR** (INSERT Predicciones con CreadoUtc UTC) o
     **NO APOSTAR** (INSERT Evaluaciones con motivo)
3. Telegram llega al instante (equipos, OVER/UNDER, linea, cuota, unidades, edge)
4. Al finalizar el juego: verifier compara CarrerasTotales vs Linea -> GANADA/PERDIDA/PUSH.
   Regla de integridad: si CreadoUtc > hora de inicio real -> **NO_VALIDA**
5. La web se regenera SOLO cuando cambia el contenido ("sin cambios (no se reescribe)")

### FIX CRITICO de idempotencia (commit f4d83f0 — no perder al replicar)
En modo ventana (`ventana_min > 0 and not retroactivo`), `pares_ya_ejecutados(fecha)` omite los
partidos ya marcados `ejecutado` en horarios.json. Sin este filtro, ticks posteriores RE-EVALUAN
y RE-INSERTAN el pick PENDIENTE con CreadoUtc fresco -> el verifier lo mata como NO_VALIDA
(fue exactamente el bug del pick Rays 1036 del 14/08/2026).

---

## 6. REGLAS DE DECISION (constantes en predecir_hoy.py)

```
MARGEN_MIN_PROB     = 0.055   # zona de senal por probabilidad: >=55.5% OVER, <=44.5% UNDER
EDGE_MINIMO         = 1.45    # desacuerdo minimo (en carreras) proyeccion vs linea casino
EDGE_ALTA_CONFIANZA = 2.0     # stake sube a 1u
LINEA_FRAGIL        = 6.5     # UNDER anulado si proyeccion <= 6.5 (duelo de pitcheo, varianza)
LINEA_EXIGENTE      = 9.5     # OVER anulado si proyeccion >= 9.5 SIN edge direccional
LINEA_MIN_OVER      = 7.5     # contradiccion: OVER exige proyeccion >= 7.5
LINEA_MAX_UNDER     = 9.0     # contradiccion: UNDER exige proyeccion <= 9.0
LINEA_MAXIMA_OVER   = 12.5 ; MARGEN_PROYECCION_EXTREMA = 2.0 (vs media del estadio)
WHIP_UMBRAL_VOLATILIDAD = 1.25 ; fatiga bullpen penaliza 0.25/0.5/1.0 segun dias
CUOTA_ODDS_FALLBACK = 1.91 ; SIGMA_TOTAL = 4.8 (ruido OOT para trasladar prob entre lineas)
```

- **Regla .5**: linea entera -> media linea A FAVOR (OVER -0.5 / UNDER +0.5): jamas hay push;
  el juego que cae exactamente en la linea cuenta GANADA.
- Stakes: 0.5u normal; 1u con edge >= 2.0; post-descanso prolongado limita a 0.5u.
- Direccion: P(decision) >= 55.5% -> OVER; <= 44.5% -> UNDER; sino Diferencia >= 1.45 -> OVER;
  sino NO BET ("Sin señal direccional").
- Regresion al mercado: la prob del modelo se mezcla con la implicita de las cuotas; el peso del
  mercado crece con el desacuerdo (evita apostar ciegamente contra lineas eficientes).
- Otros filtros defensivos: viento desfavorable para OVER, fatiga bullpen 3d/5d, WHIP del
  abridor volatil, ampayer target encoding, matchup LHP/RHP vs OPS, descanso corto/largo.

## 7. MODELO (entrenamiento)

- Target binario `Target_Over`; dataset GameLog **solo con temperatura** (~31.5k filas).
- Features: rachas (5 juegos), win rate (10), fatiga bullpen (3d/5d via vwFatigaBullpen3d),
  ERAs de abridores (ultimas3 .35 / aproximada .25 / temporada .40; abridor .60 + bullpen .40),
  matchup LHP/RHP vs OPS, ampayer target encoding (prior global), viento one-hot + velocidad,
  temperatura, park factors.
- Split **cronologico 80/20**: train hasta ~2024-06-11; test = lo mas reciente INCLUYENDO ayer.
  El XGBoost aprende del 80% viejo; el 20% nuevo sirve para metricas honestas y para ajustar la
  calibracion isotonica que se aplica en produccion. Metricas actuales: accuracy test 54.8%,
  ROC AUC 57.4%. (El ~90% de la web viene de los filtros, no del clasificador base.)
- Se reentrena TODOS los dias a las ~07 UTC con los datos de ayer incluidos.
- Artefactos guardados: `modelo_mlb_totales.pkl`, `calibracion_totales.pkl`,
  `transformadores_totales.pkl`, `columnas_totales.pkl`.

## 8. WEB Y LECTURA DE TABLAS

- `index.html` estatico + `data.json` { resumen, partidos_hoy, predicciones, evaluaciones }.
- Tabla picks: Fecha/Partido/Apuesta/Linea/Unidades/Edge/Cuota/Resultado.
- Tabla descartados (evaluaciones): Fecha/Partido/Linea/Prediccion/P(Over)/Edge/Motivo.
  - P(Under) = 100 - P(Over) (por eso solo hay una columna de probabilidad).
  - Edge en AMBAS tablas esta en CARRERAS (|proyeccion - linea|); el "%" que le pega la web a la
    tabla de picks es COSMETICO y enganoso — corregir en la version NBA.
  - Motivo vacio = ese partido SI paso los filtros (se convirtio en pick).
- "Actualizado" solo cambia cuando cambia el CONTENIDO de data.json, no en cada tick.

## 9. GUÍA DE ADAPTACION A NBA

Cambiar:
- **Fuente historica**: `nba_api` (stats.nba.com, gratis) o balldontlie -> GameLog_NBA
  (equipo, puntos totales, descanso, b2b, localia).
- **Features** (no existen clima/parques/ampayer/LHP): back-to-backs, 3-en-4, viajes/ husos,
  pace (posesiones), eFG%/TS%, rotacion/bench minutes, **injury report** (clave en NBA),
  descanso asimétrico entre equipos, arena factor.
- **Odds**: The Odds API cubre NBA totals con la misma key; snapshots igual (--solo-odds).
- **Target/calibracion**: Over/Under sobre linea de mercado (210-240 pts); recalibrar
  SIGMA/varianza (basketball != baseball); conservar esquema binario + isotonica.
- **Calendario**: oct-jun, juegos casi diarios y nocturnos ET -> el matinal puede ser UNico;
  la ventana de 30 min funciona igual.
- Renombrar todo mlb->nba (db, workflows, secrets opcionales: OpenWeather ya no hace falta).

Reutilizar SIN cambios de diseño: `db_utils`, `planificador`, `verificar_*`, `generar_web`,
`notificar_picks`, `horarios.json`, los 4 workflows, los crons de cron-job.org (crear PAT nuevo),
el bot de Telegram (o uno nuevo), y el FIX de idempotencia (pares_ya_ejecutados).

## 10. CHECKLIST DE PUESTA EN MARCHA NBA

1. Crear repo publico (ej. `nba-predictor`) con la misma estructura de carpetas
2. Copiar/adaptar workflows (renombrar mlb->nba, ajustar horarios de crons)
3. Crear secretos: THE_ODDS_API_KEY (misma), DISPATCH_PAT (PAT NUEVO), TELEGRAM_* (nuevos o mismos)
4. Programar cron-job.org cada 5 min hacia el workflow del runner nuevo
5. Backfill historico de temporadas NBA (varias) a la BD con clima omitido
6. Primer entrenamiento -> revisar accuracy/AUC honestos del split cronologico
7. Correr unos dias en modo observacion (sin apostar) comparando descartados vs resultados
8. Habilitar Pages y verificar deploy con pages-manual
