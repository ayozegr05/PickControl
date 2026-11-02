// ==UserScript==
// @name         ControlPick — Cosecha OddsPortal
// @namespace    controlpick
// @version      0.31
// @description  Cosecha cuotas de cierre de cuotasahora/oddsportal y las envía al backend de ControlPick para resolver el backlog histórico.
// @match        https://www.cuotasahora.com/*
// @match        https://www.oddsportal.com/*
// @grant        GM_xmlhttpRequest
// @grant        GM_setValue
// @grant        GM_getValue
// @connect      *
// @run-at       document-idle
// ==/UserScript==

/*
 * Uso: abre cualquier página de cuotasahora.com, pulsa "Iniciar cosecha"
 * en el panel flotante y deja la pestaña quieta. El script, por cada
 * evento pendiente:
 *   1. navega a /search/{equipo-local}/ (buscador propio del sitio),
 *   2. abre la pestaña "Resultados" y localiza el partido (visitante),
 *   3. entra a la página del partido, extrae las tablas de cuotas y
 *      las POSTea al backend (/odds-harvest/event).
 * El progreso vive en localStorage — la cosecha continúa sola entre
 * navegaciones. "Debug" copia al portapapeles la estructura que el
 * extractor ve en la página actual.
 */
(function () {
  'use strict';

  const API_BASE = 'https://controlpick.duckdns.org/api/v1';
  const HARVEST_KEY = '1980fd9b74dda35fd13e869c3b78b0578696071b50e47527';

  const STATE_KEY = 'cp_harvest_state';
  const NAV_DELAY_MS = 2500;
  const HYDRATE_MS = 7000;    // espera a que el SPA pinte los datos
  const HYDRATE_POLL_MS = 700;
  const HYDRATE_MAX_MS = 30000; // pestañas en 2º plano se hidratan lento

  // ---------------------------------------------------------------- utils

  const state = () => GM_getValue(STATE_KEY, { queue: [], idx: 0, running: false });
  const save = (s) => GM_setValue(STATE_KEY, s);
  // Log persistente: el <pre> se recrea en cada navegación, así que
  // las líneas también viven en storage (tope ~250).
  const LOG_KEY = 'cp_harvest_log';
  const log = (msg) => {
    const line = `${new Date().toLocaleTimeString()} ${msg}`;
    const el = document.getElementById('cp-harvest-log');
    if (el) el.textContent = `${line}\n` + el.textContent;
    const lines = GM_getValue(LOG_KEY, []);
    lines.unshift(line);
    GM_setValue(LOG_KEY, lines.slice(0, 250));
    console.log('[CP-Harvest]', msg);
  };
  const status = (t) => {
    const el = document.getElementById('cp-harvest-status');
    if (el) el.textContent = t;
  };

  function api(method, path, body) {
    return new Promise((resolve, reject) => {
      GM_xmlhttpRequest({
        method,
        url: `${API_BASE}${path}`,
        headers: { 'Content-Type': 'application/json', 'X-Harvest-Key': HARVEST_KEY },
        data: body ? JSON.stringify(body) : undefined,
        timeout: 30000, // sin esto una llamada colgada congela la cosecha
        onload: (r) => {
          try { resolve({ status: r.status, json: JSON.parse(r.responseText) }); }
          catch { resolve({ status: r.status, json: null }); }
        },
        onerror: (e) => reject(new Error(JSON.stringify(e))),
        ontimeout: () => reject(new Error('timeout 30s')),
      });
    });
  }

  const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

  // Deporte interno -> primer segmento de path del comparador: filtra
  // cruces de deporte (sin esto "lille" pillaba hockey, "paris" voley).
  const SPORT_SLUG = {
    futbol: 'football', tenis: 'tennis', baloncesto: 'basketball',
  };
  const sportPath = (sport) => `/${SPORT_SLUG[sport] || 'football'}/`;

  const fold = (s) =>
    (s || '').toLowerCase().normalize('NFD').replace(/[̀-ͯ]/g, '');

  // Espera hasta que el SPA hidrate contenido real. En páginas de
  // partido la zona de cuotas carga async (skeletons aria-busy /
  // animate-pulse): se espera a que desaparezcan o a que aparezcan
  // celdas con pinta de cuota.
  async function waitHydrated(matchLinks) {
    const t0 = Date.now();
    while (Date.now() - t0 < HYDRATE_MAX_MS) {
      await sleep(HYDRATE_POLL_MS);
      if (isMatchPage()) {
        const busy = document.querySelector('[aria-busy="true"]');
        const oddsish = [...document.querySelectorAll('div,span')]
          .some((el) => el.children.length === 0 &&
                        /^\d{1,3}\.\d{2}$/.test(el.textContent.trim()));
        // aria-busy desaparece ANTES de que cargue la tabla de cuotas
        // (el skeleton de arriba sí hidrata primero): sin filas de
        // tabla ni cuotas aún no hay nada que cosechar.
        const tableRows = document.querySelectorAll('table tbody tr').length;
        if (oddsish || (tableRows >= 2 && !busy)) return;
        continue;
      }
      const links = [...document.querySelectorAll('a[href]')];
      if (matchLinks ? links.some(matchLinks) : links.length > 10) return;
    }
  }

  const slugify = (s) =>
    fold(s).trim().replace(/[^a-z0-9]+/g, '-').replace(/^-+|-+$/g, '');

  // ---------------------------------------------------- descubrimiento
  // Página de búsqueda propia: /search/{equipo}/ — pestaña "Resultados"
  // con los partidos jugados del equipo. El enlace del partido casa
  // por nombre del visitante en la fila.

  // Tokens "fuertes" de un nombre para casar slugs con sinónimos
  // ("atlético de madrid" -> "atletico-madrid", "la coruna" -> "deportivo").
  const TEAM_STOP = new Set(
    ['real', 'de', 'la', 'el', 'los', 'las', 'fc', 'cf', 'club', 'ud', 'cd',
     'sd', 'sk', 'sv', 'sc', 'ac', 'as', 'united', 'city', 'deportivo',
     'atletico', 'athletic', 'femenino', 'women', 'u19', 'u21', 'b']);
  function teamTokens(name) {
    return fold(name).replace(/\s*\(f\)\s*/i, '')
      .split(/[^a-z0-9]+/)
      .filter((t) => t.length >= 4 && !TEAM_STOP.has(t));
  }

  // Alias tipster -> token del sitio ("oporto"->porto, "parís"->psg).
  const TEAM_ALIAS = {
    paris: ['psg'],
    oporto: ['porto'],
    coruna: ['deportivo'],
    'la coruna': ['deportivo'],
    munchen: ['bayern'],
    'al ahly': ['ahly'],
  };

  // Un equipo "casa" si su nombre completo, su slug, algún token
  // fuerte o un alias aparece en el texto/enlace del candidato.
  function teamHit(name, hay) {
    const f = fold(name).replace(/\s*\(f\)\s*/i, '');
    const slug = slugify(name);
    if ((f && hay.includes(f)) || (slug && hay.includes(slug))) return true;
    const extra = TEAM_ALIAS[f] || [];
    // Tokens con límite de palabra: "lille" NO debe casar "lillehammer".
    const tok = (t) => new RegExp(`(^|[^a-z0-9])${t}([^a-z0-9]|$)`).test(hay);
    return extra.some(tok) || teamTokens(name).some(tok);
  }

  function findResultLink(ev) {
    // Los enlaces h2h llevan los slugs de ambos equipos:
    //   /football/h2h/arsenal-hA1Zm19f/everton-KluSTr9s/#G0XLh1up
    // Exige el deporte correcto y que AMBOS equipos casen — con un
    // solo equipo pillaba cualquier partido del buscado (o de otro
    // deporte) y el backend rechazaba el evento cosechado.
    const sp = sportPath(ev.sport);
    const pool = [...document.querySelectorAll('a[href]')]
      .filter((a) => /\/h2h\//.test(a.href) &&
                     new URL(a.href).pathname.startsWith(sp))
      .filter((a) => {
        const hay = fold(a.textContent + ' ' + a.href);
        return teamHit(ev.home, hay) && teamHit(ev.away, hay);
      })
      .map((c) => ({ a: c }));
    let chosen = null;
    if (pool.length) {
      // La más cercana a la fecha del pick si la fila la muestra.
      // Formatos del sitio: "18.10.2026" (tooltip Forma del equipo)
      // y "18/oct." (filas de Resultados/Próximos).
      const MON = { ene: 0, feb: 1, mar: 2, abr: 3, may: 4, jun: 5, jul: 6,
                    ago: 7, sep: 8, sept: 8, oct: 9, nov: 10, dic: 11 };
      const target = new Date(ev.date).getTime();
      let bestDist = Infinity;
      for (const c of pool) {
        const row = c.a.closest('tr, li, div, section');
        const txt = row ? row.textContent : c.a.textContent;
        const dm = txt.match(/\b(\d{1,2})[.\/-](\d{1,2})(?:[.\/-](\d{2,4}))?\b/) ||
                   txt.match(/\b(\d{1,2})\s*\/\s*([a-z]{3,4})\b\.?(?:[,/ ]*\s*(\d{4}))?/i);
        if (!dm) continue;
        const month = /^\d+$/.test(dm[2]) ? +dm[2] - 1
                                          : MON[fold(dm[2])];
        if (month === undefined) continue;
        const year = dm[3] ? (dm[3].length === 2 ? 2000 + +dm[3] : +dm[3])
                           : new Date().getFullYear();
        const d = new Date(year, month, +dm[1]).getTime();
        if (Math.abs(d - target) < bestDist) { bestDist = Math.abs(d - target); chosen = c.a; }
      }
      if (!chosen) chosen = pool[0].a;
    }
    if (!chosen) {
      // Guarda qué enlaces vio el script para depurar el fallo de match.
      GM_setValue('cp_harvest_debug_search', JSON.stringify({
        url: location.href, ev,
        links: [...document.querySelectorAll('a[href]')]
          .filter((a) => /\/h2h\/|match|partido/.test(a.href))
          .slice(0, 30)
          .map((a) => `${a.textContent.trim().slice(0, 70)} -> ${a.href}`),
      }, null, 2));
    }
    return chosen ? chosen.href : null;
  }

  // Página de equipo: /{sport}/team/{slug}/{id}/ — su histórico de
  // resultados es mucho más largo que la pestaña Resultados de la
  // búsqueda. El (slug, id) sale de cualquier enlace h2h del equipo:
  // /h2h/{slug}-{id8}/{slug}-{id8}/.
  const H2H_TEAMS =
    /\/h2h\/([a-z0-9-]+?)-([A-Za-z0-9]{8})\/([a-z0-9-]+?)-([A-Za-z0-9]{8})/i;

  function findTeamLink(ev) {
    const sp = sportPath(ev.sport);
    for (const a of document.querySelectorAll('a[href]')) {
      const m = a.href.match(H2H_TEAMS);
      if (!m || !new URL(a.href).pathname.startsWith(sp)) continue;
      for (const [slug, id] of [[m[1], m[2]], [m[3], m[4]]]) {
        if (teamHit(ev.home, slug) || teamHit(ev.away, slug)) {
          return `${sp}team/${slug}/${id}/`;
        }
      }
    }
    // Respaldo: enlace /team/ directo si la página lo trae — del
    // MISMO deporte (la búsqueda mezcla: "real madrid" saca primero
    // el equipo de basket y acabábamos en /basketball/team/…).
    const hit = [...document.querySelectorAll('a[href*="/team/"]')]
      .filter((a) => new URL(a.href).pathname.startsWith(sp))
      .find((a) => teamHit(ev.home, fold(a.textContent + ' ' + a.href)) ||
                  teamHit(ev.away, fold(a.textContent + ' ' + a.href)));
    return hit ? hit.href : null;
  }

  async function handleSearchPage(myToken) {
    try {
      await _handleSearchPage(myToken);
    } catch (e) {
      log(`  error en listado: ${e.message || e} — salto`);
      const s2 = state();
      s2.idx += 1; save(s2);
      setTimeout(() => nextStep(s2.token), NAV_DELAY_MS);
    }
  }

  async function _handleSearchPage(myToken) {
    const s = state();
    if (!s.running || (myToken && s.token !== myToken)) return;
    const ev = s.queue[s.idx];
    if (!ev) { save({ ...s, running: false }); return; }
    // La pestaña "Resultados" puede ser un tab clickable: se pulsa si
    // existe. Primero espera a que la búsqueda hidrate de verdad —
    // nav siempre tiene >10 links, hay que esperar a los de partido.
    await waitHydrated((a) => /\/h2h\//.test(a.href));
    const tabs = [...document.querySelectorAll('a,button,[role="tab"],li,span')]
      // Fuera del cromo de la página: un "Resultados" de menú/aside
      // navegaría a otra sección entera.
      .filter((t) => !t.closest('nav,aside,header,footer'));
    const resTab =
      tabs.find((t) => /^\s*resultados?\s*$/i.test(t.textContent.trim())) ||
      tabs.find((t) => /resultados|results/i.test(t.textContent) &&
                       !/pr[oó]ximos/i.test(t.textContent));
    // En /search/ la pestaña "Resultados" es un <a href="/search/results/…">
    // DENTRO del <li>: clickar el li no navega nada (los clicks no bajan
    // a los hijos) y la página se quedaba en "Próximos partidos" — por
    // eso ningún partido pasado aparecía. En página de equipo es un
    // <button> React: click real sobre el botón, no el li.
    const resLi = resTab?.closest('li') || resTab;
    // En búsquedas SIN resultados la SPA deja "Próximos partidos"
    // marcado aunque la URL ya sea /search/results/…: clickar el tab
    // navegaba a la misma URL → bucle de recargas infinito y mudo.
    // Ya estar en el pathname del tab cuenta como activo.
    const resTabHref = resTab?.matches('a')
      ? resTab.href
      : resTab?.querySelector('a[href]')?.href;
    const onResultsPath =
      resTabHref && new URL(resTabHref).pathname === location.pathname;
    const resActive =
      onResultsPath ||
      (resLi && /active-item-feed|border-orange-main/.test(resLi.className));
    if (resTab && !resActive) {
      const inner = resTab.matches('a,button')
        ? resTab
        : resTab.querySelector('a[href],button');
      if (inner?.tagName === 'A' && inner.href) {
        location.href = inner.href;
        return;
      }
      realClick(inner || resTab);
      await waitHydrated((a) => /\/h2h\//.test(a.href));
      await sleep(1500);
    }
    const url = findResultLink(ev);
    if (!url) {
      // Cadena de rescate (una vez cada una por evento):
      //   1) tarjeta de equipo -> su página lista más resultados
      //   2) búsqueda por el visitante (el slug del local puede fallar)
      if (s.triedTeamFor !== s.idx) {
        const teamLink = findTeamLink(ev);
        if (teamLink) {
          s.triedTeamFor = s.idx;
          save(s);
          log(`  sin enlace — pruebo la página del equipo`);
          location.href = teamLink;
          return;
        }
      }
      if (s.triedAwayFor !== s.idx && slugify(ev.away)) {
        s.triedAwayFor = s.idx;
        save(s);
        log(`  sin enlace — pruebo búsqueda por "${ev.away}"`);
        location.href = `/search/results/${slugify(ev.away)}/`;
        return;
      }
      log(`  ${ev.home} vs ${ev.away}: sin enlace en Resultados — salto (DEBUG guardado)`);
      const s2 = state();
      s2.idx += 1; save(s2);
      setTimeout(() => nextStep(s2.token), NAV_DELAY_MS);
      return;
    }
    location.href = url;
  }

  // --------------------------------------------------------- extractor
  // Genérico: recorre las tablas de cuotas renderizadas; el nombre de
  // mercado sale del tab activo. Cuando el DOM real difiera, "Debug"
  // muestra lo que el script ve para ajustar selectores.

  // Pestaña de mercado activa: <li class="tab-item ... active-item-feed">
  // con el nombre en un span interior ("1X2", "Más/Menos de"...).
  function activeMarketName() {
    const tab = document.querySelector('li.tab-item.active-item-feed');
    if (!tab) return '';
    const span = tab.querySelector('span.font-bold') || tab.querySelector('span');
    return (span?.textContent || tab.textContent).trim();
  }

  // Nombre de la casa: <p>1xBet.es</p> en la primera celda; si no, el
  // slug de su enlace /proxy/bookmakers/{slug}/ o el title del bono.
  function bookieName(td) {
    const p = td.querySelector('p');
    if (p && p.textContent.trim() && !/^bookmakers?$/i.test(p.textContent.trim()))
      return p.textContent.trim();
    const a = td.querySelector('a[href*="/bookmakers/"]');
    if (a) {
      if (a.title) return a.title;
      const m = a.href.match(/bookmakers\/([^/]+)/);
      if (m) return m[1];
    }
    return '';
  }

  // Celda de cuota: el precio vigente es el texto "N.NN" NO tachado;
  // el tachado (.line-through) es el precio anterior — sirve de
  // referencia de apertura aproximada. Sin precio vivo = retirada.
  function cellOdds(cell) {
    const live = [...cell.querySelectorAll('a,p,span')]
      .filter((el) => el.children.length === 0 &&
                      !el.classList.contains('line-through') &&
                      !el.closest('.line-through'))
      .map((el) => el.textContent.trim())
      .filter((t) => /^\d{1,3}\.\d{2}$/.test(t));
    const struck = [...cell.querySelectorAll('.line-through')]
      .map((el) => el.textContent.trim())
      .filter((t) => /^\d{1,3}\.\d{2}$/.test(t));
    return {
      closing: live.length ? parseFloat(live[live.length - 1]) : null,
      opening: struck.length ? parseFloat(struck[0]) : null,
    };
  }

  function extractRows(market) {
    market = market || activeMarketName();
    const rows = [];
    for (const table of document.querySelectorAll('table')) {
      const ths = [...table.querySelectorAll('thead th')]
        .map((th) => th.textContent.trim());
      let labels = ths.slice(1);
      // La última columna es el "Payout" (%) de la casa — no es cuota.
      if (/payout|pago/i.test(labels[labels.length - 1] || '')) labels.pop();
      // Tabla por líneas (hándicap, O/U, totales): la primera columna
      // NO es "Bookmakers" sino la propia línea ("Hándicap asiático
      // -1.75", "Más/Menos de +2.5"; el nº suelto del final de la
      // celda es el contador de casas) y la cuota visible es la mejor
      // entre ellas. Sin bookie por fila: "cuotasahora" (agregado).
      const lineTable = ths.length > 0 && !/^bookmakers?$/i.test(ths[0]);
      const ouTable = /m[aá]s\s*\/\s*menos|over.?under/i.test(market);
      let line = null;
      for (const tr of table.querySelectorAll('tbody tr')) {
        // Fila-cabecera de línea ("Más/Menos de 2.5", colspan).
        const head = tr.querySelector('[colspan]');
        if (head && tr.cells.length <= 2) {
          const m = head.textContent.match(/([+-]?\d+(?:[.,]\d+)?)/);
          if (m) line = parseFloat(m[1].replace(',', '.'));
          continue;
        }
        const cells = [...tr.cells];
        if (cells.length < 2) continue;
        if (lineTable) {
          const lm0 = cells[0].textContent.match(/([+-]?\d+(?:[.,]\d+)?)/);
          if (!lm0) continue;
          const rowLine = parseFloat(lm0[1].replace(',', '.'));
          cells.slice(1, 1 + labels.length).forEach((cell, i) => {
            const { closing, opening } = cellOdds(cell);
            if (closing === null || closing <= 1.0) return;
            let sel = labels[i] || `${i + 1}`;
            // En O/U las columnas pueden ser "1"/"2" genéricas: la
            // primera es Más (Over), la segunda Menos (Under).
            if (ouTable && sel === '1') sel = 'Más';
            else if (ouTable && sel === '2') sel = 'Menos';
            rows.push({
              market,
              selection: sel,
              line: rowLine,
              closing,
              opening,
              bookmaker: 'cuotasahora',
            });
          });
          continue;
        }
        const bookmaker = bookieName(cells[0]);
        if (!bookmaker) continue;
        cells.slice(1, 1 + labels.length).forEach((cell, i) => {
          const { closing, opening } = cellOdds(cell);
          if (closing === null || closing <= 1.0) return;
          // La etiqueta real de la opción puede vivir en un title
          // dentro de la celda ("1X", "Más de") — las cabeceras de
          // columna a veces son iconos genéricos 1/X/2.
          const own = cell.querySelector('[title]')?.getAttribute('title')?.trim() ||
                      cell.getAttribute('title')?.trim() || '';
          const sel = own.length > 0 && own.length <= 20 ? own
                                                          : (labels[i] || `col${i}`);
          // La línea puede venir dentro de la cabecera ("Más de 12.5")
          // si no hubo fila colspan con ella.
          const lm = line === null &&
            (labels[i] || '').match(/(\d+(?:[.,]\d+)?)/);
          rows.push({
            market,
            selection: sel,
            line: line ?? (lm ? parseFloat(lm[1].replace(',', '.')) : null),
            closing,
            opening,
            bookmaker,
          });
        });
      }
    }
    return rows;
  }

  // Cosecha TODAS las pestañas de mercado visibles: el SPA cambia la
  // tabla al clicarlas sin recargar la página.
  const MARKET_TAB_RE =
    /1x2|local\/visitante|m[aá]s\/menos|h[aá]ndicap|ambos|doble|empate|descanso|par\/impar|total|ganador|winner|juegos|games|sets|puntos|points|c[oó]rners|tarjetas|cards|mapa|marcador/i;

  // Click "de verdad": muchos componentes React de este SPA escuchan
  // pointerdown/mousedown, no un click aislado — la secuencia completa
  // dispara el handler de la pestaña.
  function realClick(el) {
    // view:window revienta en el sandbox de Tampermonkey (su window es
    // un proxy, no un Window real): se omite, basta con bubbles.
    const opts = { bubbles: true, cancelable: true };
    el.dispatchEvent(new PointerEvent('pointerdown', opts));
    el.dispatchEvent(new MouseEvent('mousedown', opts));
    el.dispatchEvent(new PointerEvent('pointerup', opts));
    el.dispatchEvent(new MouseEvent('mouseup', opts));
    el.dispatchEvent(new MouseEvent('click', opts));
  }

  // IMPORTANTE: al cambiar de mercado React RE-RENDERIZA la barra de
  // pestañas — el li.tab-item que guardamos queda desconectado del
  // DOM y su classList ya no refleja nada. Por eso la activación se
  // comprueba re-consultando el documento por nombre de pestaña, no
  // sobre el nodo viejo (ese era el "no activó" masivo de la v0.21).
  function findTabByName(wanted) {
    const want = fold(wanted);
    const all = [...document.querySelectorAll('li.tab-item')];
    // Al activarse, "Más/Menos de" puede pasar a llamarse
    // "Más/Menos de 2.5" (la pestaña muestra la línea) — igualdad
    // primero, prefijo después.
    return all.find((li) => fold(li.textContent) === want) ||
           all.find((li) => fold(li.textContent).startsWith(want));
  }

  function tabIsActive(wanted) {
    return !!findTabByName(wanted)?.classList.contains('active-item-feed');
  }

  async function waitTabActive(wanted, ms) {
    const t0 = Date.now();
    while (Date.now() - t0 < ms) {
      if (tabIsActive(wanted)) return true;
      await sleep(350);
    }
    return false;
  }

  // Firma de la tabla visible: cabeceras + primera fila. Sirve para
  // detectar que el click cambió el mercado AUNQUE la pestaña se haya
  // transformado en selector de línea ("Más/Menos de" puede dejar de
  // ser un li.tab-item con ese texto al activarse).
  function tableSignature() {
    return [...document.querySelectorAll('table thead th')]
      .map((th) => th.textContent.trim()).join('|') +
      '::' + (document.querySelector('table tbody tr')?.textContent || '')
        .replace(/\s+/g, ' ').slice(0, 80);
  }

  async function waitMarketChange(wanted, sig0, ms) {
    const t0 = Date.now();
    while (Date.now() - t0 < ms) {
      if (tabIsActive(wanted) || tableSignature() !== sig0) return true;
      await sleep(350);
    }
    return false;
  }

  // Código del mercado en el hash de la URL, cuando difiere del
  // nombre de la pestaña (visto: "Doble oportunidad"→DO,
  // "Más/Menos de"→Más/Menos). Solo los confirmados en DOM real.
  const HASH_MARKET = { 'mas/menos de': 'Más/Menos' };

  async function harvestAllMarkets() {
    const rows = [];
    // Todas las li.tab-item con pinta de mercado — INCLUIDAS las
    // ocultas ("hidden max-sm:flex"): son las entradas del "Más" y
    // se pueden activar por click sintético igualmente.
    const seenTabs = new Set();
    const names = [...document.querySelectorAll('li.tab-item')]
      .filter((li) => li.querySelector('button,a') &&
                      MARKET_TAB_RE.test(li.textContent) &&
                      !seenTabs.has(fold(li.textContent)) &&
                      seenTabs.add(fold(li.textContent)))
      .map((li) => li.textContent.trim().replace(/\s+/g, ' '));
    for (const wanted of names) {
      if (!tabIsActive(wanted)) {
        const sig0 = tableSignature();
        const li = findTabByName(wanted);
        const btn = li?.querySelector('button,a') || li;
        if (btn) realClick(btn);
        // La pestaña cuenta como activada si se marca active-item-feed
        // O si la tabla cambió (React puede convertir la pestaña en
        // un selector de línea y ya no se reencuentra por nombre).
        let activated = await waitMarketChange(wanted, sig0, 4000);
        if (!activated) {
          const li2 = findTabByName(wanted);
          if (li2) realClick(li2);
          activated = await waitMarketChange(wanted, sig0, 3000);
        }
        if (!activated && HASH_MARKET[fold(wanted)]) {
          // Última vía: el mercado va codificado en el hash
          // (#MRky3Pd3:Más/Menos;2 — visto en DOM real). El click
          // del tab de O/U en fútbol solo actualiza la URL; poner
          // el hash a mano fuerza al SPA a cargar la tabla.
          const idm = location.hash.match(/^#([A-Za-z0-9]{8})/);
          if (idm) {
            location.hash = '#' + idm[1] + ':' +
              encodeURIComponent(HASH_MARKET[fold(wanted)])
                .replace(/%2F/g, '/') + ';2';
            activated = await waitMarketChange(wanted, sig0, 4000);
          }
        }
        await sleep(1200); // deja pintar la tabla del mercado nuevo
        if (!activated && !tabIsActive(wanted)) {
          // Nunca extraer una tabla que no corresponde a la pestaña:
          // re-etiquetar 1X2 como otro mercado corrompe el auditor.
          const menu = [...document.querySelectorAll(
            '[role="menu"] *,[role="listbox"] *,[role="option"],[data-radix-popper-content-wrapper] *')]
            .map((el) => el.textContent.trim())
            .filter((t) => t && t.length < 40).slice(0, 12).join(' | ');
          log(`    pestaña "${wanted}" no activó — salto sin extraer` +
              ` (activa: "${activeMarketName() || '?'}")` +
              (menu ? ` menu=[${menu}]` : ''));
          continue;
        }
      }
      const before = rows.length;
      // El nombre de mercado es el de la pestaña clickada — la web
      // duplica la barra de tabs y activeMarketName() puede leer la
      // copia equivocada ("Local/Visitante" en basket).
      rows.push(...extractRows(wanted));
      // Diagnóstico: filas + cabeceras reales de la tabla — si ths
      // no cambia entre pestañas la tabla no cambió.
      const ths = [...document.querySelectorAll('table thead th')]
        .map((th) => th.textContent.trim()).join(',');
      log(`    pestaña "${wanted}" → +${rows.length - before} filas ` +
          `ths=[${ths}]`);
    }
    return rows;
  }

  // OJO: el JSON-LD SportsEvent describe el PRÓXIMO cruce entre los
  // equipos, no necesariamente el partido mostrado (visto: página
  // Betis 2-5 Barça de oct con JSON-LD de un Barça-Betis de marzo).
  // Equipos reales: los dos primeros enlaces /team/ del header, en
  // orden (local primero). Fecha real: el texto "17 oct. 2026, 17:30".
  const ES_MONTHS = {
    ene: 1, feb: 2, mar: 3, abr: 4, may: 5, jun: 6,
    jul: 7, ago: 8, sep: 9, oct: 10, nov: 11, dic: 12,
  };

  function pageTeams() {
    const names = [];
    for (const a of document.querySelectorAll('a[href*="/team/"]')) {
      const t = a.textContent.trim();
      if (t && !names.includes(t)) names.push(t);
      if (names.length === 2) break;
    }
    return names;
  }

  function pageStart() {
    const text = document.body.innerText;
    const m = text.match(
      /\b(\d{1,2})\s+(ene|feb|mar|abr|may|jun|jul|ago|sep|oct|nov|dic)\w*\.?,?\s+(\d{4})/i);
    if (m) {
      const mm = String(ES_MONTHS[m[2].slice(0, 3).toLowerCase()]).padStart(2, '0');
      const dd = String(m[1]).padStart(2, '0');
      return `${m[3]}-${mm}-${dd}T12:00:00Z`;
    }
    const d2 = text.match(/\b(\d{1,2})[.\/-](\d{1,2})[.\/-](\d{4})\b/);
    return d2 ? `${d2[3]}-${d2[2].padStart(2, '0')}-${d2[1].padStart(2, '0')}T12:00:00Z`
            : null;
  }

  function extractEvent() {
    const [home, away] = pageTeams();
    return {
      url: location.href.split('#')[0] + '#' + (location.hash.match(/^#([A-Za-z0-9]{8})/)?.[1] || ''),
      home: home || '',
      away: away || '',
      start: pageStart(),
      rows: [],
    };
  }

  // ----------------------------------------------------------- flujo

  function isMatchPage() {
    // Página de partido real: /{deporte}/h2h/{eq}-{id}/{eq}-{id}/...#matchId
    // (NUNCA /results/... — es el índice de ligas, antes colaba por
    // "result" y el script cosechaba una página de archivo).
    return /\/h2h\//.test(location.pathname);
  }
  function isSearchPage() {
    return /^\/search\//.test(location.pathname);
  }
  // Página de equipo: /{sport}/team/{slug}/{id}/ — también es un
  // listado con pestaña Resultados, lo maneja handleSearchPage.
  function isTeamPage() {
    return /\/team\//.test(location.pathname);
  }

  async function harvestCurrentPage(myToken) {
    try {
      await _harvestCurrentPage(myToken);
    } catch (e) {
      // Una excepción silenciosa aquí congelaba la cosecha sin log:
      // se registra y se salta al siguiente evento.
      log(`  error en página de partido: ${e.message || e} — salto`);
      const s2 = state();
      s2.idx += 1; save(s2);
      setTimeout(() => nextStep(s2.token), NAV_DELAY_MS);
    }
  }

  async function _harvestCurrentPage(myToken) {
    const s = state();
    if (!s.running || (myToken && s.token !== myToken)) return;
    const ev = s.queue[s.idx];
    if (!ev) { save({ ...s, running: false }); return; }
    status(`[${s.idx + 1}/${s.queue.length}]`);
    // Guardia de deporte: la navegación pudo caer en un h2h de otro
    // deporte (voley/basket...). Mejor salto que datos cruzados.
    if (!location.pathname.startsWith(sportPath(ev.sport))) {
      log(`  página de otro deporte (${location.pathname.slice(0, 40)}) — salto`);
      s.idx += 1; save(s);
      setTimeout(() => nextStep(s.token), NAV_DELAY_MS);
      return;
    }
    await waitHydrated();
    // Página todavía en skeleton (aria-busy/animate-pulse y ni los
    // enlaces de equipo del header): la hidratación del SPA se
    // atascó — una recarga suele desbloquearla. 1 vez por evento.
    const stillSkeleton =
      !document.querySelector('a[href*="/team/"]') &&
      document.querySelector('[aria-busy="true"], .animate-pulse');
    if (stillSkeleton && s.reloadedFor !== s.idx) {
      s.reloadedFor = s.idx;
      save(s);
      log('  página aún en skeleton — recargo una vez');
      location.reload();
      return;
    }
    const data = extractEvent();
    // Guardia previa al POST: la página debe ser EL partido del pick —
    // ambos equipos y fecha a ±1 día. Sin esto cosechaba otra edición
    // del cruce (Francia-Bélgica del 5 oct con pick del 17 oct).
    const hay = fold(`${data.home} ${data.away} ${data.url}`);
    const days = data.start
      ? Math.abs(new Date(data.start) - new Date(ev.date)) / 864e5
      : Infinity;
    if (!(teamHit(ev.home, hay) && teamHit(ev.away, hay)) || days > 1.5) {
      log(`  página no casa con el pick (${data.home || '?'} vs ${data.away || '?'}` +
          `, ${data.start ? data.start.slice(0, 10) : 'sin fecha'}) — salto sin POST`);
      saveDebugSample();
      const sk = state();
      sk.idx += 1; save(sk);
      setTimeout(() => nextStep(sk.token), NAV_DELAY_MS);
      return;
    }
    data.sport = ev.sport;
    data.rows = await harvestAllMarkets();
    if (!data.home || !data.away) { data.home = ev.home; data.away = ev.away; }
    if (!data.start) data.start = `${ev.date}T12:00:00Z`;
    if (data.rows.length === 0) {
      // Página de partido sin filas extraídas: guarda la muestra del
      // DOM para depurar el extractor (se copia luego con "Debug").
      saveDebugSample();
      log(`POST ${ev.home} vs ${ev.away}: 0 filas — DEBUG guardado`);
    } else {
      log(`POST ${ev.home} vs ${ev.away}: ${data.rows.length} filas`);
    }
    log(`  página: ${data.home} vs ${data.away} ${data.url}`);
    try {
      const res = await api('POST', '/odds-harvest/event', data);
      log(`  -> ${res.status} ${res.json ? res.json.detail || '' : '(sin detalle)'}`);
    } catch (e) {
      log(`  -> error POST: ${e.message || e}`);
    }
    const s2 = state();
    s2.idx += 1; save(s2);
    setTimeout(() => nextStep(s2.token), NAV_DELAY_MS);
  }

  async function nextStep(myToken) {
    const s = state();
    // El token aborta cadenas duplicadas: solo sigue la ejecución que
    // inició el último "Iniciar cosecha".
    if (!s.running || (myToken && s.token !== myToken)) { status('parado'); return; }
    const ev = s.queue[s.idx];
    if (!ev) {
      save({ ...s, running: false });
      status('fin');
      log('COSECHA TERMINADA');
      return;
    }
    status(`[${s.idx + 1}/${s.queue.length}]`);
    log(`[${s.idx + 1}/${s.queue.length}] ${ev.home} vs ${ev.away} (${ev.date})`);
    // Qué piden los picks del evento: guía para verificar después si
    // el mercado necesario apareció entre las filas cosechadas.
    if (ev.picks && ev.picks.length) {
      log('    piden: ' + ev.picks
        .map((p) => `${p.mercado} ${p.seleccion || ''} ${p.linea ?? ''}`.trim())
        .join(' | '));
    }
    // Watchdog: si este evento sigue siendo el actual pasados 150 s,
    // la cadena se congeló en algún punto (espera sin tope, página
    // zombie...). Invalidar el token mata la cadena vieja si sigue
    // viva y salta al siguiente.
    const evIdx = s.idx, evToken = s.token;
    setTimeout(() => {
      const cur = state();
      if (cur.running && cur.token === evToken && cur.idx === evIdx) {
        cur.token = Date.now();
        cur.idx += 1;
        save(cur);
        log('  paso congelado (>150s) — salto forzado');
        setTimeout(() => nextStep(cur.token), NAV_DELAY_MS);
      }
    }, 150000);

    const slug = slugify(ev.home) || slugify(ev.away);
    if (!slug) {
      log(`  nombre inservible para búsqueda — salto`);
      s.idx += 1; save(s);
      setTimeout(() => nextStep(s.token), NAV_DELAY_MS);
      return;
    }
    // /search/results/{slug}/ aterriza directo en la pestaña de
    // resultados pasados (en /search/{slug}/ el tab activo es
    // "Próximos partidos" y el histórico nunca aparece).
    const searchUrl = `/search/results/${slug}/`;
    if (isSearchPage() || isTeamPage()) {
      handleSearchPage(s.token); // estamos ya en el listado pedido
    } else {
      location.href = searchUrl;
    }
  }

  // Vuelca lo que el extractor ve en la página actual: celdas con
  // pinta de cuota + HTML de la zona central. Se guarda en storage
  // para poder copiarlo más tarde (la cosecha no espera a nadie).
  function debugPayload() {
    const main = document.querySelector('main') || document.body;
    const oddsCells = [...document.querySelectorAll('div,span,p,a')]
      .filter((el) => el.children.length === 0 && /^\d{1,3}\.\d{2}$/.test(el.textContent.trim()))
      .slice(0, 40)
      .map((el) => {
        const row = el.parentElement?.parentElement;
        return `${el.textContent.trim()} | row: ${row ? row.textContent.slice(0, 90) : ''}`;
      }).join('\n');
    return JSON.stringify(
      { url: location.href, market: activeMarketName(), oddsCells,
        html: main.innerHTML.slice(0, 30000) },
      null, 2
    );
  }
  function saveDebugSample() {
    GM_setValue('cp_harvest_debug_match', debugPayload());
  }

  // ------------------------------------------------------------- UI

  function panel() {
    const div = document.createElement('div');
    div.style.cssText =
      'position:fixed;bottom:12px;right:12px;z-index:2147483647;background:#111;color:#eee;' +
      'padding:10px 12px;border-radius:8px;font:12px/1.4 monospace;max-width:360px;' +
      'box-shadow:0 2px 10px rgba(0,0,0,.5)';
    div.innerHTML =
      '<b>ControlPick Harvest</b> <span id="cp-harvest-status">—</span><br>' +
      '<button id="cp-harvest-go">Iniciar cosecha</button> ' +
      '<button id="cp-harvest-stop">Parar</button> ' +
      '<button id="cp-harvest-debug" title="Vuelca lo que el script ve">Debug</button> ' +
      '<button id="cp-harvest-copylog" title="Copia el log completo">Log</button>' +
      '<pre id="cp-harvest-log" style="max-height:150px;overflow:auto;margin:6px 0 0"></pre>';
    document.body.appendChild(div);
    // Restaura las últimas líneas guardadas (el DOM se resetea al navegar).
    const storedLog = GM_getValue(LOG_KEY, []);
    if (storedLog.length) {
      document.getElementById('cp-harvest-log').textContent =
        storedLog.slice(0, 25).join('\n');
    }
    document.getElementById('cp-harvest-go').onclick = async () => {
      try {
        const res = await api('GET', '/odds-harvest/queue');
        if (!res.json || !res.json.events) { log(`queue error HTTP ${res.status}`); return; }
        const token = Date.now();
        GM_setValue(LOG_KEY, []); // log limpio por cosecha
        save({ queue: res.json.events, idx: 0, running: true,
               token, triedAwayFor: -1, triedTeamFor: -1, reloadedFor: -1 });
        log(`cola: ${res.json.events.length} eventos`);
        nextStep(token);
      } catch (e) { log(`queue error: ${e.message || e}`); }
    };
    document.getElementById('cp-harvest-stop').onclick = () => {
      save({ ...state(), running: false });
      status('parado');
      log('parado por el usuario');
    };
    document.getElementById('cp-harvest-debug').onclick = () => {
      // Lo que se ve AHORA en pantalla manda; la muestra guardada
      // solo es fallback para cuando la página ya no tiene contenido
      // útil (navegación a medias, pestaña muerta).
      const matchStored = GM_getValue('cp_harvest_debug_match', '');
      const searchStored = GM_getValue('cp_harvest_debug_search', '');
      const live = debugPayload();
      const payload = live.length > 800 ? live
                    : (matchStored || searchStored || live);
      navigator.clipboard.writeText(payload);
      log(`debug copiado (${payload.length} chars)`);
    };
    document.getElementById('cp-harvest-copylog').onclick = () => {
      const stored = GM_getValue(LOG_KEY, []);
      const dom = document.getElementById('cp-harvest-log').textContent;
      const txt = stored.length ? stored.join('\n') : dom;
      navigator.clipboard.writeText(txt);
      log('log copiado al portapapeles');
    };
  }

  // Arranque: panel + retomar la cosecha según la página en que caímos.
  panel();
  const s0 = state();
  if (s0.running) {
    status(`[${s0.idx + 1}/${s0.queue.length}]`);
    if (isMatchPage()) harvestCurrentPage(s0.token);
    else if (isSearchPage() || isTeamPage()) handleSearchPage(s0.token);
    else setTimeout(() => nextStep(s0.token), NAV_DELAY_MS);
  } else {
    status('parado');
  }
})();
