# Auditoría de la app — hallazgos (2026-11-02)

Auditoría completa del frontend Expo/React Native (`Frontend/PickControl/`)
hecha sobre la app real corriendo en emulador Android (Pixel 7 Pro API 34,
dev-client + Metro apuntando a producción) con una cuenta de auditoría
(`Devin Audit`) recién registrada, más revisión exhaustiva de código.

Leyenda: 🔴 crítico · 🟡 mejorable · 🔵 cosmético · ✅ resuelto

---

## 1. Críticos (seguridad / datos)

- [ ] 🔴 **Fuga de datos multiusuario en lecturas de apuestas.** Visto en
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
- [ ] 🔴 **Navegación por `router.push` en la BottomBar** —
  `src/components/bottom-bar.tsx:25-58`. Cada tap apila una pantalla
  nueva: el botón atrás se convierte en una trampa y el stack crece
  indefinidamente. Migrar a `Tabs` de expo-router (o al menos
  `router.navigate`/`replace`).
- [ ] 🔴 **Post-registro roto** (confirmado en vivo): tras "Registro
  Exitoso" el usuario queda autenticado pero la app navega a
  `/screens/login` → ve el formulario de login estando ya logueado.
  `app/screens/register.tsx:55` → debe ser `router.replace("/")`.
- [ ] 🔴 **Ruta dinámica sin codificar** —
  `router.push(\`/dynamic-routes/${nombre}\`)` con nombres de canal como
  `"Dm7 || GRATUITO || ⚡🔞"` (espacios, pipes, emojis): rompe el
  deep-link con `%`, `#`, `?`. En `index.tsx:66`, `auditoria.tsx:133`,
  `analysis.tsx:305`, `my-picks.tsx:127`, `parsed-picks.tsx:563`.
  Fix: `router.push({ pathname: "/dynamic-routes/[informante]",
  params: { informante: nombre } })`.
- [ ] 🔴 **`pick.informante` puede ser `null`** → genera la ruta literal
  `/dynamic-routes/null` (`parsed-picks.tsx:563`; el tipo lo admite:
  `parsed-picks.api.ts`). Guard antes del push.
- [ ] 🔴 **`earns` nunca refresca**: `dataLoaded` bloquea el refetch de
  por vida del componente (`earns.tsx:30,45,93`) — datos obsoletos tras
  añadir/liquidar apuestas. Usar `useFocusEffect` como el resto.
- [ ] 🔴 **Contraseña en claro en SecureStore** (`userCredentials`) para
  el login biométrico (`login.tsx:127-133`). SecureStore cifra, pero
  persistir la password es anti-patrón; si el usuario la cambia, el
  biométrico falla sin feedback. Guardar token de larga duración.
- [ ] 🔴 **Sin manejo global de 401 ni timeout** (`client.ts:66-87`):
  fetch sin `AbortSignal` → petición colgada cuelga la pantalla; JWT
  caducado = sesión "zombie" (autenticado pero todo falla). Solo
  `add-pick` redirige manualmente. Fix: `AbortSignal.timeout(15000)` +
  interceptor 401 → logout + redirect a login.
- [ ] 🔴 **Datos mal etiquetados en `analysis`**: `slice(-30)` etiquetado
  "Último mes" son las últimas 30 apuestas, no un mes
  (`analysis.tsx:106`); "2 semanas"/"anteriores" son 14 picks
  (`127-128`) → la "tendencia" mostrada mide otra cosa de lo que dice.

## 2. UI / UX (verificado visualmente en emulador)

- [ ] 🟡 **Cuatro paletas mezcladas**: naranja/dark de marca vs verde
  brillante (CTA login/register, spinner de informante) vs tabla
  verde+beige con texto negro (my-picks e historial de
  `[informante].tsx` — parece otra app) vs rojo-anaranjado ("Enviar
  Apuesta"). Unificar en un `theme.ts`.
- [ ] 🟡 **Tabla de historial recortada a la derecha** (`[informante]`,
  `my-picks`): columnas cortadas dentro de scroll horizontal, fechas en
  formato US (`7/28/2026`) mientras el resto usa `17/10/2026`.
- [ ] 🟡 **El teclado tapa el CTA** (confirmado: "Registrarse" quedó
  oculto y hubo que cerrar el teclado para enviar). Sin
  `KeyboardAvoidingView` ni `keyboardShouldPersistTaps` en
  login/register/add-pick/modal "Yo también la jugué".
- [ ] 🟡 **BottomBar visible en login/register** — un usuario sin sesión
  ve la navegación de la app (7 iconos). Además: sin estado activo,
  sin labels, tamaños inconsistentes (24–38px), color `"orange"`
  literal en vez del naranja de marca.
- [ ] 🟡 **Controles admin/liquidación visibles para cualquier usuario**:
  botones Acertó/Falló/Anulada/Pendiente por pick en parsed-picks y
  "Corregir picks de Telegram" en el detalle. Verificar que el backend
  valida rol (y ocultarlos si no eres admin).
- [ ] 🟡 **Accesibilidad inexistente**: 0 `accessibilityLabel/Role` —
  checkboxes custom, iconos-botón (papelera, corrección de patas, ojo
  de password) invisibles para TalkBack. Contraste flojo
  (`#777/#888` a 11-13px sobre `#1a1a1a` ≈ 3.5:1, bajo WCAG AA).
- [ ] 🟡 `expo-status-bar` instalado pero **nunca usado** — sin
  `<StatusBar style="light" />`.
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

- [ ] 🔴 **Archivos gigantes**: `[informante].tsx` 2046 líneas,
  `parsed-picks.tsx` 1687, `my-picks.tsx` 911, `analysis.tsx` 586 —
  lógica de dominio (ROI, filtros, formato, iconos) dentro de los
  componentes. Extraer `utils/`, `hooks/`, subcomponentes.
- [ ] 🔴 **Cero `FlatList`/`SectionList`** en todo el proyecto:
  `ScrollView + map` incluso en tablas con cientos de filas; my-picks
  anida 3 ScrollViews. Cuello de botella real con el backlog actual.
- [ ] 🟡 **Sin tema centralizado**: `#ff9f1c` hardcodeado ~50 veces +
  variantes (`"orange"`, `#ffba57`, `#f3b02b`, `#FF4500`); verdes en
  ≥7 valores; fondos `"black"`/`#000`/`#0d0d0d`/`#1a1a1a`/`#2d2d2d`.
  Cada pantalla repite `container/card/input/title` casi idénticos.
- [ ] 🟡 **Duplicación de utilidades** (mientras `src/utils/` y
  `src/hooks/` están vacíos): `colorForYield`×3, `cleanChannel`×2,
  `calcularGanancia`×3, `formatearFecha`×2, iconos de estado×3.
- [ ] 🟡 **Dead code**: `index.js` raíz (Stack sin providers),
  `src/components/piechart.tsx` (mock, nunca importado),
  `calcularGananciaAcumulada`/`agruparDatos` (`[informante]:222,374`),
  `screenWidth`/`Dimensions` sin uso, estado `apuestas` en `earns`,
  `loadingRef`, `scrollY`/`visible` en top-bar (listener de un
  Animated.Value nunca conectado a un scroll), `handleScroll`/`showTopBar`
  entero en my-picks, estilos copiados de index en my-picks sin uso.
- [ ] 🟡 **`try/catch` alrededor de JSX** (`index.tsx:71-164`) — no
  captura errores de render; el `catch` es inalcanzable.
- [ ] 🟡 ~40 `console.log` (alguno en cada render), `any`×28,
  `as any` en 5 `router.push` pese a `typedRoutes: true`.
- [ ] 🟡 **Fetchs sin cancelación** en la mayoría de pantallas
  (`setTimeout(500)` sin clear en `analysis.tsx:89`); solo
  `[informante]` usa flag `cancelled` — generalizarlo en un hook.
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

- [ ] 🟡 **Cero tests** en el frontend. `jest-expo` configurado pero sin
  `moduleNameMapper` para el alias `@/` → cualquier test que toque
  `@/src/...` falla al importar. Script `"test"` entra en modo watch
  (malo para CI). Empezar por mappers puros (`toPickItem`, `toCanal`),
  `extractErrorMessage` y las utils cuando se extraigan.

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

1. ✅→ 🔒 **Aislamiento de datos** (`/apuestas`, `/analisis`,
   `/informante`, `/informantes`): `get_current_user` + filtro por
   `usuario_id`. Backend ~20 líneas.
2. ✅→ **Auth/navegación**: post-registro `replace("/")`, interceptor
   401→logout+login, timeout 15s, params en ruta dinámica + guard null.
3. `theme.ts` + componentes `Screen/Card/Input/PrimaryButton` +
   retirar paleta verde/beige + StatusBar.
4. `Tabs` de expo-router + quitar BottomBar de auth + estados activos.
5. `FlatList` + extraer utils/hooks + adelgazar los >1500-liner.
6. KeyboardAvoiding + a11y labels + contraste + formatos de fecha.
7. Limpieza deps/dead code/console.log + smoke tests + eslint flat.
8. Revisar rol admin en UI (ocultar liquidación manual a no-admins).
