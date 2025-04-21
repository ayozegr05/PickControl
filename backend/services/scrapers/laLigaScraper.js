import puppeteer from 'puppeteer';
import * as cheerio from 'cheerio';


class LaLigaScraper {
    constructor() {
        this.browser = null;
        this.laLigaUrl = 'https://www.laliga.com/es-GB/laliga-easports/resultados';
        this.defaultTimeout = 60000;
    }

    async initialize() {
        if (!this.browser) {
            this.browser = await puppeteer.launch({
                headless: 'new',
                args: [
                    '--no-sandbox',
                    '--disable-setuid-sandbox',
                    '--disable-web-security',
                    '--disable-features=IsolateOrigins,site-per-process',
                    '--window-size=1920,1080'
                ]
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

            await page.goto(this.laLigaUrl, { waitUntil: 'networkidle0' });
            await new Promise(res => setTimeout(res, 4000));

            // Extrae los partidos renderizados usando page.evaluate
            const matches = await page.evaluate((date, teams) => {
                // Normaliza los nombres de equipos para filtrar
                const normalize = str => str ? str.trim().toLowerCase() : '';
                const filterTeams = (home, away, teams) => {
                    if (!teams || teams.length === 0) return true;
                    return teams.some(team =>
                        normalize(home).includes(normalize(team)) ||
                        normalize(away).includes(normalize(team))
                    );
                };

                const containers = Array.from(document.querySelectorAll('.ue-c-scoreboard-item-container'));
                return containers.map(item => {
                    const estado = item.querySelector('.ue-c-scoreboard-dual__state')?.textContent.trim();
                    if (estado !== 'Finalizado') return null;

                    const homeTeam = item.querySelector('.ue-c-scoreboard-dual__team--home-team .ue-c-scoreboard-dual__team-name')?.textContent.trim();
                    const awayTeam = item.querySelector('.ue-c-scoreboard-dual__team--away-team .ue-c-scoreboard-dual__team-name')?.textContent.trim();
                    const homeScore = item.querySelector('.ue-c-scoreboard-dual__team--home-team .ue-c-scoreboard-dual__score')?.textContent.trim();
                    const awayScore = item.querySelector('.ue-c-scoreboard-dual__team--away-team .ue-c-scoreboard-dual__score')?.textContent.trim();

                    if (!homeTeam || !awayTeam || homeScore === undefined || awayScore === undefined) return null;
                    if (!filterTeams(homeTeam, awayTeam, teams)) return null;

                    return {
                        teams: { home: { name: homeTeam }, away: { name: awayTeam } },
                        goals: { home: parseInt(homeScore, 10), away: parseInt(awayScore, 10) },
                        fixture: { date: date }
                    };
                }).filter(Boolean);
            }, date, teams);

            await page.close();
            return { response: matches };
        } catch (error) {
            console.error('Error scraping LaLiga:', error);
            return { response: [], error: error.message };
        } finally {
            await this.close();
        }
    }
}

export default new LaLigaScraper();