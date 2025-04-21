import puppeteer from 'puppeteer';
import * as cheerio from 'cheerio';

class MarcaScraper {
    constructor() {
        this.browser = null;
        this.baseUrl = 'https://www.marca.com/resultados/futbol/primera-division.html?date=';
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

            const [yyyy, mm, dd] = date.split('-');
            const url = `${this.baseUrl}${dd}-${mm}-${yyyy}`;
            await page.goto(url, { waitUntil: 'networkidle0' });
            await new Promise(res => setTimeout(res, 3000));
            const content = await page.content();
            const $ = cheerio.load(content);
            const matches = [];

            $('.mod-resultados-partido').each((i, el) => {
                const $el = $(el);
                const equipos = $el.find('.equipo-nombre').map((i, e) => $(e).text().trim()).get();
                if (equipos.length !== 2) return;

                const marcador = $el.find('.resultado').text().trim().split('-');
                const homeScore = marcador[0] ? parseInt(marcador[0].trim()) : null;
                const awayScore = marcador[1] ? parseInt(marcador[1].trim()) : null;
                const hora = $el.find('.hora').text().trim() || null;

                if (teams && teams.length > 0) {
                    const isMatch = teams.some(team =>
                        equipos[0].toLowerCase().includes(team.toLowerCase()) ||
                        equipos[1].toLowerCase().includes(team.toLowerCase())
                    );
                    if (!isMatch) return;
                }

                matches.push({
                    teams: { home: { name: equipos[0] }, away: { name: equipos[1] } },
                    goals: { home: homeScore, away: awayScore },
                    fixture: { date: date, time: hora }
                });
            });
            await page.close();
            return { response: matches };
        } catch (error) {
            console.error('Error scraping Marca:', error);
            return { response: [], error: error.message };
        } finally {
            await this.close();
        }
    }
}

export default new MarcaScraper();