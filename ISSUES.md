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
- [x] ✅ **`earns` nunca refresca** — resuelto (2026-11-03, `2c1e1c8`):
  `useFocusEffect` → `reload()` con guard de primer foco y reset del
  flag de agregación; al volver a la pestaña refetchea y re-anima con
  datos frescos.
- [x] ✅ **Contraseña en claro en SecureStore** — resuelto
  (2026-11-03, `1e31c7c`): el backend emite un refresh token de 30
  días (`type="refresh"`, `jwt_refresh_expires_minutes`) y
  `POST /auth/refresh` lo canjea por un access token con rotación;
  un access token NO vale para refrescar (test dedicado). En
  `login.tsx` solo se persiste `{refreshToken, userName, role}` y
  las credenciales legacy `{email,password}` migran en caliente en
  el primer uso. Si el refresh caduca se borra y se pide login
  manual — antes el fallo era silencioso.
  **Ampliado (mismo día)**: los refresh YA son revocables — tabla
  `refresh_tokens` (una fila por `jti`, familia = sesión de
  dispositivo) con rotación y detección de reuso: un token ya
  consumido que reaparece revoca la familia entera (robo).
  `POST /auth/logout` mata la familia de la credencial presentada;
  `logoutSession` queda en `auth.api.ts` para un futuro "cerrar
  sesión en este dispositivo" — el logout normal de la app NO la
  llama a propósito para no romper el biométrico.
- [x] ✅ **Sin manejo global de 401 ni timeout** (`client.ts`):
  fetch sin `AbortSignal` → petición colgada cuelga la pantalla; JWT
  caducado = sesión "zombie" (autenticado pero todo falla).
  **Resuelto en `c0d088c`**: `AbortSignal.timeout(15s)` +
  `setUnauthorizedHandler` → AuthContext cierra sesión y
  `router.replace("/screens/login")`.
- [x] ✅ **Datos mal etiquetados en `analysis`** — resuelto
  (2026-11-03, `2c1e1c8`): `calcularEstadisticas` filtra una ventana
  real de 30 días por `fecha` (tendencia = ROI 0–14d vs 15–28d);
  apuestas sin fecha quedan fuera. La UI muestra "Últimos 30 días".

## 2. UI / UX (verificado visualmente en emulador)

- [x] ✅ **Cuatro paletas mezcladas**: `src/theme.ts` creado
  (`colors.*` + `metricColor`). Migrados los puntos visibles: CTAs
  login/register/add-pick verde/rojo → `primary` con texto
  `onPrimary`; tablas verde+beige de my-picks e `[informante]` →
  dark surface; inputs blancos de add-pick → `colors.input` +
  texto claro. Quedan literales `#ff9f1c` sueltos (~50) — migración
  incremental al tocar cada pantalla.
- [x] ✅ **Tabla de historial recortada a la derecha** — resuelto:
  `tableContainer` de my-picks tenía `flexGrow:1` dentro del scroll
  horizontal → el contenedor se quedaba con el ancho del viewport y
  las columnas extra quedaban inalcanzables; ahora la tabla mide su
  contenido. Además `showsHorizontalScrollIndicator` pasa a `true`
  en las tres tablas (my-picks, ApuestasTable, TipsterPicksCard) —
  con el indicador oculto la última columna parecía "cortada" sin
  remedio. Fechas: los `toLocaleDateString()` sin locale de my-picks
  pasan a `formatDateES()` (DD/MM/YYYY explícito, es-ES).
- [x] ✅ **El teclado tapa el CTA** — resuelto (2026-11-03,
  `1e31c7c`): `KeyboardAvoidingView` (iOS padding + offset) +
  `keyboardShouldPersistTaps="handled"` en login, register y
  add-pick, y dentro de `ModalFrame` (cubre JugarModal y el resto
  de modales de `[informante]`).
- [x] ✅ **BottomBar visible en login/register** — resuelto: sin
  sesión la barra de tabs se oculta entera (`display: none` + tabs
  con `href: null`). Estado activo (naranja/gris), labels y tamaños
  uniformes ya vienen del Tabs nativo.
- [x] ✅ **Controles admin/liquidación visibles para cualquier usuario**
  — resuelto (2026-11-03, `2c1e1c8`): el backend era el agujero real
  (`PATCH /telegram/parsed-picks/{id}` aceptaba cualquier autenticado)
  → ahora 403 sin rol ADMIN. Frontend: prop `canCorrect` oculta los
  botones en `ParsedPickCard` y `PickDetailModal`. **Pendiente deploy
  al VM** para que el gate aplique en prod.
- [x] 🟡 **Accesibilidad**: labels en `ModalFrame` ("Cerrar"),
  `DeletePickModal`, `UpdatePickModal`, chevron de combinadas,
  checkboxes de filtros en parsed-picks (`role="checkbox"` +
  `accessibilityState`), los 4 iconos de corrección de patas y los
  botones Acertó/Falló/Anulada/Pendiente (`ParsedPickCard`), ojo de
  password en login y Switch + papelera de canales. **Contraste**:
  textos secundarios subidos a `#9a9a9a` (~5.8:1 sobre `#1a1a1a`,
  cumple AA) — solo quedaban flojos los `#777`/`#666`; los `#888`
  (~4.9:1) ya pasaban.
- [x] ✅ `expo-status-bar` instalado pero **nunca usado** — resuelto:
  `<StatusBar style="light" />` en `app/_layout.tsx`.
- [x] ✅ **Estados error/vacío inconsistentes** — resuelto
  (2026-11-03): componente compartido `src/components/inline-error.tsx`
  (`ErrorState`: icono + mensaje + Reintentar). `my-picks` ya no
  traga el error en silencio; `earns` muestra el ErrorState en vez
  del 0.00€ falso (y la animación no corre — `setIsLoading` ya no
  espera al timer); `[informante]` distingue error de red
  (`ErrorState`) de canal realmente vacío ("No se encontraron
  datos"). Bonus: la animación de earns se para al desmontar — el
  timer de 1500ms hacía setState sobre el componente muerto.
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
- [x] 🟡 `isInitializing` en AuthContext — la landing ya no pinta un
  frame en cold start: `(tabs)/index` muestra el spinner hasta que
  `checkAuth` resuelve.
- [x] 🟡 `isAdmin` leído de AsyncStorage manipulable → **derivado del
  payload del JWT firmado** (`src/utils/jwt.ts`, decoder propio sin
  `atob` compatible con Hermes); `AuthContext` calcula el rol del token
  al hidratar y tras login; `userRole` de AsyncStorage eliminado.
- [x] 🟡 `unregisterDeviceToken` existe pero nunca se llama →
  **`unregisterForPushNotifications()`** en `push.ts` (DELETE idempotente
  del token Expo en el logout, best-effort con guarda anti-recursión
  del handler 401).
- [x] 🟡 `login()`/`logout()` tragan errores de storage — `login`
  propaga el fallo al caller (que ya muestra su alerta) y no marca
  sesión sin persistencia; `logout` limpia el estado en memoria
  SIEMPRE en `finally` (antes un fallo de storage dejaba sesión
  zombie autenticada).
- [x] 🔵 Guard de auth centralizado — `src/utils/auth-guard.ts`
  (función pura `redirectTarget`, 8 contratos testeados) +
  `src/components/auth-guard.tsx` envolviendo el Stack del root
  `_layout`: sin sesión solo son públicas landing/login/register y el
  resto → `/`; con sesión, login/register → `/`. La pantalla protegida
  ni se renderiza (cero flash). Queda abierto solo lo cosmético:
  rutas `/screens/…`/`/dynamic-routes/…` feas en URL; `scheme:"myapp"`.
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
