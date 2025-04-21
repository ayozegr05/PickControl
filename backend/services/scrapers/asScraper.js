import puppeteer from 'puppeteer';
import * as cheerio from 'cheerio';

class ASScraper {
    constructor() {
        this.browser = null;
        this.baseUrl = "https://as.com/resultados/futbol/primera/";        
        this.defaultTimeout = 60000;
    }

    async initialize() {
        if (!this.browser) {
            this.browser = await puppeteer.launch({
                headless: 'new',
                args: ['--no-sandbox', '--disable-setuid-sandbox', '--disable-web-security',
                       '--disable-features=IsolateOrigins,site-per-process', '--window-size=1920,1080']
            });
        }
    }

    async close() {
        if (this.browser) {
            await this.browser.close();
            this.browser = null;
        }
    }

    async getMatchesByDate(date, teams = []) {
        try {
            await this.initialize();
            const page = await this.browser.newPage();
            await page.setViewport({ width: 1920, height: 1080 });
            await page.setUserAgent('Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36');
            await page.setDefaultNavigationTimeout(this.defaultTimeout);
            await page.setDefaultTimeout(this.defaultTimeout);
    
            const matches = [];
    
            // Si tenemos equipos específicos, buscar por la ficha de equipo
            if (teams && teams.length > 0) {
                console.log("AS Scraper: Buscando partidos para equipos específicos...");
                
                // Mapa de equipos conocidos y sus IDs
                const equipoIds = {
                    'barcelona': '3',
                    'real madrid': '1',
                    'atletico': '42',
                    'celta': '6',
                    'athletic': '5',
                    'espanyol': '8',
                    'getafe': '172',
                    'rayo': '2',
                    'valencia': '17',
                    'mallorca': '11',
                    'leganes': '132',
                    'las palmas': '9',
                    'valladolid': '18',
                    'osasuna': '13',
                    'villarreal': '19',
                    'real sociedad': '16',
                    'sevilla': '53',
                    'alaves': '4',
                    'girona': '648',
                    'betis': '171'
                    // Puedes añadir más equipos según necesites
                };
                
                // Buscar para cada equipo
                for (const team of teams) {
                    // Normalizar nombre de equipo
                    const teamNormalized = team.toLowerCase().replace(/\s+/g, ' ').trim();
                    
                    // Buscar coincidencias parciales en las claves
                    let teamKey = null;
                    for (const [key, id] of Object.entries(equipoIds)) {
                        if (teamNormalized.includes(key) || key.includes(teamNormalized)) {
                            teamKey = key;
                            console.log(`AS Scraper: Equipo ${team} coincide con ${key} (ID: ${id})`);
                            break;
                        }
                    }
                    
                    if (!teamKey) {
                        console.log(`AS Scraper: No se encontró ID para ${team}, saltando...`);
                        continue;
                    }
                    
                    const teamId = equipoIds[teamKey];
                    const teamUrl = `https://as.com/resultados/ficha/equipo/${teamKey}/${teamId}/`;
                    await page.goto(teamUrl, { waitUntil: 'networkidle0' });
                    await new Promise(res => setTimeout(res, 3000));

                    const content = await page.content();
                    const $ = cheerio.load(content);
                    
                    // Buscar partidos en la ficha del equipo
                    console.log("AS Scraper: Buscando partidos en ficha de equipo...");
                    
                    // Buscar en la tabla de resultados recientes
                    $('div.cont-resultados table.tabla-datos tbody tr').each((i, elem) => {
                        // Saltar filas que son separadores de competición
                        if ($(elem).hasClass('row-mas-info')) return;
    
                        const localTeam = $(elem).find('td.col-equipo-local .nombre-equipo').text().trim();
                        const visitorTeam = $(elem).find('td.col-equipo-visitante .nombre-equipo').text().trim();
                        const marcador = $(elem).find('td.col-resultado.finalizado .resultado').text().replace(/\s+/g, ' ').trim();
    
                        // Extraer los goles
                        let homeScore = null, awayScore = null;
                        const scoreMatch = marcador.match(/(\d+)\s*-\s*(\d+)/);
                        if (scoreMatch) {
                            homeScore = parseInt(scoreMatch[1]);
                            awayScore = parseInt(scoreMatch[2]);
                        }
    
                        // Si falta algún dato, saltar
                        if (!localTeam || !visitorTeam || homeScore === null || awayScore === null) return;
    
                        // Verificar si este partido involucra a los equipos que buscamos
                        const otherTeam = teams.find(t => 
                            t !== team && 
                            (localTeam.toLowerCase().includes(t.toLowerCase()) || 
                            visitorTeam.toLowerCase().includes(t.toLowerCase()))
                        );
                        
                        if (otherTeam) {
                            console.log(`AS Scraper: ¡Coincidencia! Partido entre ${team} y ${otherTeam}`);
                            
                            matches.push({
                                teams: { home: { name: localTeam }, away: { name: visitorTeam } },
                                goals: { home: homeScore, away: awayScore },
                                fixture: { date: date }
                            });
                        }
                    });
                    
                    // Si ya encontramos partidos, no necesitamos seguir buscando
                    if (matches.length > 0) {
                        console.log(`AS Scraper: Encontrados ${matches.length} partidos, finalizando búsqueda.`);
                        break;
                    }
                }
            }
            
            // Si no encontramos nada por ficha de equipos, intentar con la URL general
            if (matches.length === 0) {
                console.log("AS Scraper: Intentando búsqueda general...");
                await page.goto(this.baseUrl, { waitUntil: 'networkidle0' });
                await new Promise(res => setTimeout(res, 3000));
                
                const content = await page.content();
                const $ = cheerio.load(content);

                console.log("AS Scraper: Buscando partidos en tabla de resultados...");

                $('div.cont-resultados table.tabla-datos tbody tr').each((i, elem) => {
                    // Saltar filas que son separadores de competición
                    if ($(elem).hasClass('row-mas-info')) return;

                    const localTeam = $(elem).find('td.col-equipo-local .nombre-equipo').text().trim();
                    const visitorTeam = $(elem).find('td.col-equipo-visitante .nombre-equipo').text().trim();
                    const marcador = $(elem).find('td.col-resultado.finalizado .resultado').text().replace(/\s+/g, ' ').trim();

                    // Extraer los goles
                    let homeScore = null, awayScore = null;
                    const scoreMatch = marcador.match(/(\d+)\s*-\s*(\d+)/);
                    if (scoreMatch) {
                        homeScore = parseInt(scoreMatch[1]);
                        awayScore = parseInt(scoreMatch[2]);
                    }

                    // Si falta algún dato, saltar
                    if (!localTeam || !visitorTeam || homeScore === null || awayScore === null) return;

                    // Si se pasan equipos, filtra sólo los que coincidan
                    if (teams && teams.length > 0) {
                        const isMatch = teams.some(team =>
                            localTeam.toLowerCase().includes(team.toLowerCase()) ||
                            visitorTeam.toLowerCase().includes(team.toLowerCase())
                        );
                        if (!isMatch) return;
                    }

                    matches.push({
                        teams: { home: { name: localTeam }, away: { name: visitorTeam } },
                        goals: { home: homeScore, away: awayScore },
                        fixture: { date: date }
                    });

                    console.log(`AS Scraper: Partido encontrado: ${localTeam} ${homeScore} - ${awayScore} ${visitorTeam}`);
                });
            } // <- Cierre correcto del if (matches.length === 0)
            
            console.log(`AS Scraper: Total de partidos encontrados: ${matches.length}`);
            await page.close();
            return { response: matches };
        } catch (error) {
            console.error('Error scraping AS:', error);
            return { response: [], error: error.message };
        } finally {
            await this.close();
        }
    }
}

export default new ASScraper();