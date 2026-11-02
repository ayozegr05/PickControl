# Investigación cuotas — provisional (21-oct-2026)

Documento de trabajo. Estado del hito "cuotas auditadas" y opciones abiertas.

## Estado numérico (hoy)

- Picks auditables vivos: **525** (simples, no anulados, con deporte y fecha pasada)
- Con cuota auditada: **438 (83%)**
- Pendientes reales: **86** — lista completa en `pendientes_cuotas_86.txt`

## Fuentes de cuotas — mapa de opciones

### Ya integradas

| Fuente | Rol | Estado |
|---|---|---|
| BetsAPI (api.b365api.com) | Archivo bet365 — histórico | Trial agotado; drenaje hecho (152 payloads cacheados) |
| oddspapi | Mirror bet365 capado | 200 llamadas/mes; no lista UCL/femeninas |
| theoddsapi | Snapshot en vivo (alternas .5 + props) | Corriendo; solo futuro |
| sofascore/footapi7, betexplorer, tennisexplorer, footballdata(CSV), tennisdata, oddsfeed, llm-odds | Cascada histórica | Activas |

### Para recuperar el PASADO (los 86) — opciones restantes

| Fuente | Qué cubre | Coste | Veredicto |
|---|---|---|---|
| Betfair Historical Data (historicdata.betfair.com) | Exchange: O/U córners .5, BTTS, hándicaps en partidos grandes | Pago por paquetes mensuales (~£30+) | Opción de pago puntual |
| The Odds API — endpoint histórico | Incluye Pinnacle + Betfair + William Hill; alternas .5 | Solo planes de pago (~$30/mes) | Cubre .5, NO enteras |
| API-Football `/odds` | Archiva bet365 + casas por fixture | ~$19/mes (gratis: solo ±3 días — verificado) | Posible pago puntual |
| OpticOdds / SportsGameOdds / OddsBlaze | Agregadores multi-casa con histórico | Trials gratis | Verificar cobertura en trial |
| Pinnacle API | La casa sharp | **CERRADA al público desde jul-2025** (solo B2B) | Descartada |
| Búsqueda manual (OddsPortal, Asianodds, webs de casas) | Lo que cada casa publicó | Gratis | En curso — el usuario busca |

### Para el FUTURO — gratis sin explotar (la mejora grande)

| Fuente | Auth | Qué aporta vs lo actual | Riesgo |
|---|---|---|---|
| **1xBet endpoints públicos** (los de su propia web, JSON sin key) | Ninguna | **Córners ENTERAS (8.0/11.0/12.0)**, props de jugador, tarjetas, sets de tenis — todo lo que bet365 no vende | No oficial, puede cambiar sin aviso |
| **Kambi público** (`eu-offering.kambicdn.org` — Unibet/888/DraftKings) | Ninguna | Prematch+live, props en partidos grandes | No oficial |
| **Bwin API pública** (sportsapi.bwin.com) | Ninguna | Prematch+live, estándar+alternas | No oficial |
| **Betfair Exchange API** | Cuenta Betfair.es | Exchange real: O/U córners .5, props | Requiere cuenta con fondos |
| SharpAPI / apihub.io (wrappers 1xBet) | API key | 1xBet normalizado | 100 llamadas/mes gratis; luego pago |

**Nota**: los endpoints no oficiales cubren mercados en vivo/prematch — NO histórico.
Sirven para capturar picks nuevos, no para rellenar el pasado.

## Muestra por categoría para búsqueda manual

Buscar: **cuota de cierre de la LÍNEA EXACTA** (o la más cercana a la fecha del pick).
`nomarket` = bet365 tenía el partido pero NO ese mercado → mirar Pinnacle/Betfair/1xBet/Asianodds.
`nofixture` = bet365 ni listó el partido → mirar cualquier otra casa.

### Córners línea entera (cluster gordo: 33 picks)

| ID | Fecha | Partido | Pick | Tipster | bet365 |
|---|---|---|---|---|---|
| 3633 | 26-sep | Inglaterra - España | +7.0 córners | 1.50 | nomarket |
| 1177 | 13-sep | Real Sociedad - Atlético | +8.0 córners | 1.50 | nomarket |
| 2234 | 20-sep | Atlético - Real Madrid | +8.0 córners | 1.50 | nomarket |
| 868 | 16-sep | Atlético - Osasuna | -11.0 córners | 1.50 | nomarket |
| 1419 | 18-sep | Espanyol - Elche | -11.0 córners | 1.50 | nomarket |
| 2542 | 21-sep | Aldosivi - Atl. Tucumán | -12.0 córners | 1.50 | nomarket |
| 7859 | 14-oct | Roma - Real Madrid | -12.0 córners | 1.50 | nomarket |
| 9341 | 20-oct | Stuttgart - Atlético | -12.0 córners | 1.50 | nomarket |

### Tenis "juegos del set" (12 picks)

| ID | Fecha | Partido | Pick | bet365 |
|---|---|---|---|---|
| 2289 | 1-sep | Wallin - Nikles | +7.5 juegos 1er set | nomarket |
| 1718 | 16-sep | Schoolkate - Gengel | +7.5 juegos 1er set | nomarket |
| 1670 | 19-sep | Bergs - Rodionov | +6.5 juegos 1er set | nomarket |
| 9151 | 19-oct | Herbert - Mrva | +9.5 juegos 2º set | nofixture |

### Props jugador / especiales (~17 picks)

| ID | Fecha | Partido | Pick | bet365 |
|---|---|---|---|---|
| 322 | 15-sep | Alavés - Valencia | +3 tarjetas partido | nomarket |
| 6143 | 6-oct | Suecia - Polonia | Gyökeres anota | nofixture |
| 6144 | 6-oct | Suecia - Polonia | Svensson ve tarjeta | nofixture |
| 8481 | 14-oct | Betis - Oporto | Diogo Costa 4+ paradas | nomarket |
| 8482 | 14-oct | Betis - Oporto | Nelson Deossa amonestación | nomarket |

### Estándar — sospechosos de fallo de mapeo nuestro

| ID | Fecha | Partido | Pick | Nota |
|---|---|---|---|---|
| 7157 | 11-oct | Midtjylland - Copenhague | Ambos anotan @1.53 | BTTS es estándar — verificar si bet365 lo tenía (posible bug mapper) |
| 6233 | 7-oct | Aris - S.P. Burgos | Aris gana | Moneyline estándar — idem |
| 8846 | 5-sep | Real Madrid - Bilbao | RM gana @1.12 | Ganador estándar — idem |

### Otros

| ID | Fecha | Partido | Pick | bet365 |
|---|---|---|---|---|
| 4549 | 30-sep | Lyon Fem - Chelsea Fem | +2.5 goles @1.60 | nofixture (UWCL) |
| 2746 | 22-sep | Arsenal Fem - Koge Fem | +2.5 goles @1.50 | nofixture (UWCL) |
| 1868 | 2-sep | Saint-Truidense - Union SG | +2.0 goles | nomarket (línea entera) |
| 578 | 17-ago | Albot - Rossi | Albot gana @1.50 | nofixture (ITF Futures) |
| 6452 | 8-oct | Bayern - Virtus | Bayern -1.5 @1.50 | basket hándicap |
| 7762 | 14-oct | Hapoel TA - Besiktas | Besiktas +10.5 @1.50 | basket hándicap |
| 1803 | 6-sep | GP Italia | -19.5 coches @1.66 | F1: nadie lo vende |
| 4481 | 30-sep | (Filin vs Aidan Mayo) | +7.5 juegos | Ya resuelto ganado; solo etiqueta |

## Próximos pasos abiertos

- [ ] Usuario: búsqueda manual de cuotas de cierre en webs de casas
- [ ] Si BTTS/ganador basket aparecen en bet365 → bug en mapper BetsAPI, arreglar (gratis)
- [ ] Decidir: provider 1xBet/Kambi en vivo (gratis, futuro)
- [ ] Decidir: trial OpticOdds/SGO o pago puntual Betfair Historical para el pasado
