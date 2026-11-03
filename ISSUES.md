# Auditoría de la app — hallazgos (2026-11-02)

Auditoría completa del frontend Expo/React Native (`Frontend/PickControl/`)
hecha sobre la app real corriendo en emulador Android (Pixel 7 Pro API 34,
dev-client + Metro apuntando a producción) con una cuenta de auditoría
(`Devin Audit`) recién registrada, más revisión exhaustiva de código.

Leyenda: 🔴 crítico · 🟡 mejorable · 🔵 cosmético · ✅ resuelto

---

## 1. Críticos (seguridad / datos)

- [x] ✅ **Fuga de datos multiusuario en lecturas de apuestas.** Visto en
  vivo: una cuenta nueva sin apuestas ve las apuestas reales de otros
  usuarios (importes, casas, resultados). Endpoints afectados:
  - `GET /apuestas` — `backend/app/api/v1/picks.py:63` hace
    `select(Pick)` SIN `get_current_user` ni filtro por `usuario_id`
    → cualquiera en internet puede volcar todas las apuestas.
  - `GET /analisis` — `analisis.py:71` agrega TODOS los `Pick` en el
    bloque "yo" → las stats "Yo" de cualquier cuenta son las de todos.
  - `GET /informante/{nombre}` — `informantes.py:132` incluye las
    `apuestas` (manuales) de todos los usuarios y las agrega en
    `manual_stats` → la columna "Tú" del detalle enseña datos ajenos.
  - `GET /informantes` — `informantes.py:259` mezcla las apuestas de
    todos los usuarios en `manual_*` y en el total combinado del
    ranking ("Manual: N", yield combinado).
  Fix: `get_current_user` + filtrar `Pick.usuario_id == current_user.id`
  en los cuatro; el lado tipster (parsed_picks) queda igual.
  **Resuelto en `c0d088c`** — verificado en vivo: la cuenta de auditoría
  pasó de ver "Tú: 2 picks · 5.00€" ajenas a "0 / 0.00€" propias.
- [x] ✅ **Navegación por `router.push` en la BottomBar** — resuelto:
  `app/(tabs)/_layout.tsx` usa `Tabs` nativo de expo-router (sin
  apilar, con estado activo + labels). `bottom-bar.tsx` eliminado de
  las 11 pantallas. Rutas `/screens/X` → `/X`; tipos regenerados.
- [x] ✅ **Post-registro roto** (confirmado en vivo): tras "Registro
  Exitoso" el usuario queda autenticado pero la app navega a
  `/screens/login` → ve el formulario de login estando ya logueado.
  `app/screens/register.tsx:55` → `router.replace("/")`. `c0d088c`
- [x] ✅ **Ruta dinámica sin codificar** —
  `router.push(\`/dynamic-routes/${nombre}\`)` con nombres de canal como
  `"Dm7 || GRATUITO || ⚡🔞"` (espacios, pipes, emojis): rompe el
  deep-link con `%`, `#`, `?`. En `index.tsx:66`, `auditoria.tsx:133`,
  `analysis.tsx:305`, `my-picks.tsx:127`, `parsed-picks.tsx:563`.
  **Resuelto con `params` en `c0d088c`** — la navegación a
  "Dm7 || GRATUITO || ⚡🔞" verificada en vivo.
- [x] ✅ **`pick.informante` puede ser `null`** → genera la ruta literal
  `/dynamic-routes/null` (`parsed-picks.tsx:563`). Guard añadido. `c0d088c`
- [ ] 🔴 **`earns` nunca refresca**: `dataLoaded` bloquea el refetch de
  por vida del componente (`earns.tsx:30,45,93`) — datos obsoletos tras
  añadir/liquidar apuestas. Usar `useFocusEffect` como el resto.
- [ ] 🔴 **Contraseña en claro en SecureStore** (`userCredentials`) para
  el login biométrico (`login.tsx:127-133`). SecureStore cifra, pero
  persistir la password es anti-patrón; si el usuario la cambia, el
  biométrico falla sin feedback. Guardar token de larga duración.
- [x] ✅ **Sin manejo global de 401 ni timeout** (`client.ts`):
  fetch sin `AbortSignal` → petición colgada cuelga la pantalla; JWT
  caducado = sesión "zombie" (autenticado pero todo falla).
  **Resuelto en `c0d088c`**: `AbortSignal.timeout(15s)` +
  `setUnauthorizedHandler` → AuthContext cierra sesión y
  `router.replace("/screens/login")`.
- [ ] 🔴 **Datos mal etiquetados en `analysis`**: `slice(-30)` etiquetado
  "Último mes" son las últimas 30 apuestas, no un mes
  (`analysis.tsx:106`); "2 semanas"/"anteriores" son 14 picks
  (`127-128`) → la "tendencia" mostrada mide otra cosa de lo que dice.

## 2. UI / UX (verificado visualmente en emulador)

- [x] ✅ **Cuatro paletas mezcladas**: `src/theme.ts` creado
  (`colors.*` + `metricColor`). Migrados los puntos visibles: CTAs
  login/register/add-pick verde/rojo → `primary` con texto
  `onPrimary`; tablas verde+beige de my-picks e `[informante]` →
  dark surface; inputs blancos de add-pick → `colors.input` +
  texto claro. Quedan literales `#ff9f1c` sueltos (~50) — migración
  incremental al tocar cada pantalla.
- [ ] 🟡 **Tabla de historial recortada a la derecha** (`[informante]`,
  `my-picks`): columnas cortadas dentro de scroll horizontal, fechas en
  formato US (`7/28/2026`) mientras el resto usa `17/10/2026`.
- [ ] 🟡 **El teclado tapa el CTA** (confirmado: "Registrarse" quedó
  oculto y hubo que cerrar el teclado para enviar). Sin
  `KeyboardAvoidingView` ni `keyboardShouldPersistTaps` en
  login/register/add-pick/modal "Yo también la jugué".
- [x] ✅ **BottomBar visible en login/register** — resuelto: sin
  sesión la barra de tabs se oculta entera (`display: none` + tabs
  con `href: null`). Estado activo (naranja/gris), labels y tamaños
  uniformes ya vienen del Tabs nativo.
- [ ] 🟡 **Controles admin/liquidación visibles para cualquier usuario**:
  botones Acertó/Falló/Anulada/Pendiente por pick en parsed-picks y
  "Corregir picks de Telegram" en el detalle. Verificar que el backend
  valida rol (y ocultarlos si no eres admin).
- [ ] 🟡 **Accesibilidad** (progreso parcial 2026-11): añadidos
  `accessibilityLabel` en `ModalFrame` ("Cerrar"), `DeletePickModal`
  (papelera), botones icon-only de `UpdatePickModal` (✓/✗) y chevron
  de combinadas en `TipsterPicksCard`. Siguen sin etiqueta:
  checkboxes custom, corrección de patas, ojo de password, toggles.
  Contraste flojo (`#777/#888` a 11-13px sobre `#1a1a1a` ≈ 3.5:1).
- [x] ✅ `expo-status-bar` instalado pero **nunca usado** — resuelto:
  `<StatusBar style="light" />` en `app/_layout.tsx`.
- [ ] 🟡 **Estados error/vacío inconsistentes**: `my-picks` traga el
  error (`catch → console.error`, sin UI); `earns` muestra `0.00€`
  como dato real si falla; `[informante]` muestra "No se encontraron
  datos" también para errores de red.
- [ ] 🟡 `useVideoPlayer` se instancia siempre aunque el usuario esté
  autenticado y no vea la landing (`index.tsx:36-43`) — vídeo en loop
  innecesario; además `marginBottom: 655` mágico + `position:absolute`
  → overflow en pantallas pequeñas.
- [ ] 🔵 "Anade tu apuesta" sin tilde (`add-pick.tsx`).
- [ ] 🔵 Chip de canal truncado ("Apuesta Diaria CopetePick") en
  parsed-picks; sección "Añadir por enlace o id" queda medio tapada
  por la BottomBar en canales.
- [ ] 🔵 "Mi Perfil" es un botón sin `onPress` (`top-bar.tsx:159`).
- [ ] 🔵 Deportes duplicados en Análisis: "Automovilismo" **y**
  "Fórmula 1" como categorías separadas (mismo deporte, dos etiquetas
  en BD); deportes con 0/0 ensucian la lista.
- [ ] 🔵 `useState<string>("Pending")` para `acierto` y reset a `""`
  inválido fuera de las opciones del picker (`add-pick.tsx:30,145`);
  permite enviar con `apuesta` vacía ("(sin nombre)"), `cantidad=0` y
  `cuota=1` silenciosos (`add-pick.tsx:128-136`).

## 3. Arquitectura / mantenibilidad

- [x] ✅ **Archivos gigantes** — resuelto en la Fase D (2026-11-03):
  `[informante].tsx` 2046 → 756, `parsed-picks.tsx` 1687 → 447
  (11 componentes extraídos a `src/components/`: `ParsedPickCard`,
  `PickDetailModal`, `JugarModal`, `UpdatePickModal`, `DeletePickModal`,
  `ModalFrame`, `ApuestasTable`, `TipsterVsYouCard`, `TipsterPicksCard`,
  `admin-panels` ×4, `ChannelChips`, `ChannelSection`), más
  `src/utils/providers.ts` con las filas del panel admin como funciones
  puras. `my-picks.tsx` 853 y `analysis.tsx` 477 tras las fases A-C.
- [x] ✅ **`FlatList`/`SectionList`** — resuelto en la Fase C (2026-11-03):
  lista de canal de parsed-picks virtualizada con `onEndReached`,
  tablas de my-picks e `[informante]` (simples + "Mis apuestas") con
  `FlatList` en contenedor acotado.
- [ ] 🟡 **Sin tema centralizado**: `#ff9f1c` hardcodeado ~50 veces +
  variantes (`"orange"`, `#ffba57`, `#f3b02b`, `#FF4500`); verdes en
  ≥7 valores; fondos `"black"`/`#000`/`#0d0d0d`/`#1a1a1a`/`#2d2d2d`.
  Cada pantalla repite `container/card/input/title` casi idénticos.
- [x] ✅ **Duplicación de utilidades** — resuelto en Fase A/B
  (2026-11): `src/utils/format.ts` (`cleanChannel`, `formatDate*`),
  `picks.ts` (`gananciaApuesta`, `iconoAnulada`), `estadisticas.ts`
  (`metricColor`/`nivelRiesgo`/agregaciones), `providers.ts`; hooks
  `useApiData`/`usePicks`/`useInformantes`/`useInformanteStats`/
  `useParsedPicks` en `src/hooks/` — todas las pantallas cableadas.
- [ ] 🟡 **Dead code** (parcialmente resuelto 2026-11): quitados
  `calcularGananciaAcumulada`/`agruparDatos`/`screenWidth`/`Dimensions`
  y los estilos de chart de `[informante]` (el LineChart ya no
  existía en el JSX), `renderPronostico`, ~90 estilos huérfanos de
  los dos monstruos y el `console.log` de render de my-picks.
  Quedan: `index.js` raíz, `scrollY`/`visible` en top-bar,
  `handleScroll`/`showTopBar` en my-picks, estilos copiados de index.
- [ ] 🟡 **`try/catch` alrededor de JSX** (`index.tsx:71-164`) — no
  captura errores de render; el `catch` es inalcanzable.
- [ ] 🟡 ~40 `console.log` (alguno en cada render), `any`×28,
  `as any` en 5 `router.push` pese a `typedRoutes: true`.
- [x] ✅ **Fetchs sin cancelación** — resuelto en Fase B: `useApiData`
  cancela updates al desmontar y refetchea si cambia el fetcher;
  todas las pantallas lo usan. Además `usePicks`/`useInformantes`
  devuelven referencia estable (bug real: array nuevo por render
  causaba render-storm en el `useEffect([apuestas])` de my-picks —
  pillado por los tests).
- [ ] 🟡 Keys inseguras `key={index}` en listas (`my-picks:307,326,427,471`,
  `parsed-picks:988`).
- [ ] 🟡 Sin estado `isInitializing` en AuthContext → la landing con
  vídeo parpadea un frame en cada cold start.
- [ ] 🟡 `isAdmin` leído de AsyncStorage manipulable; mejor derivarlo
  del JWT o de un endpoint `/me`.
- [ ] 🟡 `unregisterDeviceToken` existe pero nunca se llama → tras
  logout el dispositivo sigue recibiendo pushes.
- [ ] 🟡 `login()`/`logout()` tragan errores de storage.
- [ ] 🔵 Sin guard de auth centralizado (solo my-picks usa Redirect);
  rutas `/screens/…` y `/dynamic-routes/…` feas en URL; `scheme:"myapp"`.
- [ ] 🔵 Tipos repartidos entre `src/types/` y los `.api.ts`;
  `PUT /apuesta/{id}` singular vs `/apuestas` plural.

## 4. Dependencias / configuración

- [ ] 🟡 **Sin uso** (verificado por grep): `dotenv`+`expo-dotenv`+
  `expo-env` (las 3; Expo carga `.env` nativo), `victory-native`
  (solo la usa piechart muerto), `numeral`, `prop-types`,
  `@lottiefiles/dotlottie-react`, `react-native-webview`, `expo-blur`,
  `expo-haptics`, `expo-symbols`, `expo-system-ui`, `expo-web-browser`,
  `expo-font` (plugin + ttf sin `useFonts`), `@types/yup` (yup 1.x trae
  tipos), `expo-build-tools` (tercero sin referencias).
- [ ] 🟡 **`google-services.json` referenciado en `app.json:21` pero
  fuera del repo** (ni en `.gitignore`) → build con FCM depende de un
  archivo invisible. Documentar o mover a EAS secrets.
- [ ] 🟡 **`npm run lint` probablemente no aplica config**: `.eslintrc.json`
  legacy vs flat config esperada por `expo lint`; `react-hooks` en
  plugins pero sin reglas activadas (`rules-of-hooks`/`exhaustive-deps`
  no se evalúan).
- [ ] 🟡 `metro.config.js` monkey-patchea `util.styleText` sin comentario.
- [ ] 🟡 `react-native.config.js` vacío; `scripts/reset-project.js` y
  README boilerplate de create-expo-app.
- [ ] 🔵 Verificar `typescript ~6.0.3` contra el template Expo.

## 5. Tests

- [x] ✅ **Tests** (2026-11): 153 tests en 22 suites —
  utils/hooks/api/componentes/pantallas (RNTL 14). Alias `@/`
  mapeado; `@react-native/jest-preset` instalado como peer de
  jest-expo; `IS_REACT_ACT_ENVIRONMENT` en setup y `fireEvent`
  awaited (RNTL 14 los hace async — sin await corrumpían el
  render del test siguiente). Pendiente: script no-watch para CI.

## 6. Lo que está bien (mantener)

- La honestidad de datos llega a la UI: "Cuota: - | Stake: -" sin
  inventar, "fuera de ventana — ya no se reintenta", "Método: rule |
  Confianza: 0.8", CLV y "% cuotas infladas" en auditoría.
- Validación de formularios real (Yup+Formik): "Las contraseñas deben
  coincidir" pillado en vivo.
- Token en SecureStore con migración en caliente (`storage/secure.ts`).
- Tema oscuro + naranja consistente en las pantallas principales;
  donuts y rank badges funcionan bien.
- `useFocusEffect` en home; flag `cancelled` en `[informante]`.
- Endpoints de escritura ya hardened (PUT/DELETE owner-or-admin).

---

## Plan de ataque (prioridad × esfuerzo)

1. ✅ **Aislamiento de datos** (`/apuestas`, `/analisis`,
   `/informante`, `/informantes`): `get_current_user` + filtro por
   `usuario_id`. Hecho en `c0d088c` + desplegado en el VM.
2. ✅ **Auth/navegación**: post-registro `replace("/")`, interceptor
   401→logout+login, timeout 15s, params en ruta dinámica + guard null.
   Hecho en `c0d088c`.
3. ✅ `theme.ts` + componentes base + retirar paleta verde/beige +
   StatusBar.
4. ✅ `Tabs` de expo-router + quitar BottomBar de auth + estados activos.
5. ✅ `FlatList` + extraer utils/hooks + adelgazar los >1500-liner —
   hecho en las fases A–D (2026-11): utils, hooks `useApiData`,
   virtualización y 11 componentes extraídos, todo test-first.
6. KeyboardAvoiding + a11y labels (parcial: modales) + contraste +
   formatos de fecha.
7. Limpieza deps/dead code/console.log (parcial) + smoke tests
   (hecho: 153 tests) + eslint flat.
8. Revisar rol admin en UI (ocultar liquidación manual a no-admins).
