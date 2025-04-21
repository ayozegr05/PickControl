import { MatchResult } from '../DbMongo/model.js';
import laLigaScraper from './scrapers/laLigaScraper.js';
import marcaScraper from './scrapers/marcaScraper.js';
import asScraper from './scrapers/asScraper.js';

class ResultsService {
    constructor() {
        // Lista de scrapers en orden de prioridad
        this.scrapers = [
            { name: 'LaLiga', scraper: laLigaScraper },
            { name: 'Marca', scraper: marcaScraper },
            { name: 'AS', scraper: asScraper }
        ];
    }

    async findMatch(teams, date) {
        console.log(`🔍 Buscando partido: ${teams.join(' vs ')} (${date})`);

        // 1. Primero buscar en la base de datos
        try {
            const cachedResult = await this.findInDatabase(teams, date);
            if (cachedResult) {
                console.log('✅ Partido encontrado en caché');
                return cachedResult;
            }
        } catch (error) {
            console.error('❌ Error buscando en base de datos:', error);
        }

        // 2. Si no está en la base de datos, intentar cada scraper
        for (const { name, scraper } of this.scrapers) {
            try {
                console.log(`🌐 Intentando scraper: ${name}`);
                const result = await scraper.getMatchesByDate(date, teams);
                
                if (result && result.response && result.response.length > 0) {
                    console.log(`✅ Partido encontrado en ${name}`);
                    
                    // Guardar en la base de datos para futuras consultas
                    await this.saveToDatabase(result.response[0], name);
                    
                    return result;
                }
            } catch (error) {
                console.error(`❌ Error con scraper ${name}:`, error);
                continue; // Intentar con el siguiente scraper
            }
        }

        console.log('❌ No se encontró el partido en ninguna fuente');
        return { response: [], error: 'No encontrado' };
    }

    async findInDatabase(teams, date) {
        const startOfDay = new Date(date);
        startOfDay.setHours(0, 0, 0, 0);
        
        const endOfDay = new Date(date);
        endOfDay.setHours(23, 59, 59, 999);

        // Buscar partido que coincida con los equipos y la fecha
        const match = await MatchResult.findOne({
            $and: [
                {
                    $or: [
                        { 'teams.home.name': { $in: teams } },
                        { 'teams.away.name': { $in: teams } }
                    ]
                },
                { date: { $gte: startOfDay, $lte: endOfDay } }
            ]
        });

        if (match) {
            // Verificar si los datos están actualizados (menos de 1 hora)
            const oneHourAgo = new Date(Date.now() - 60 * 60 * 1000);
            if (match.updatedAt > oneHourAgo) {
                return { response: [match] };
            }
        }

        return null;
    }

    async saveToDatabase(match, sourceName) {
        try {
            const matchData = {
                teams: {
                    home: {
                        name: match.teams.home.name,
                        score: match.goals?.home || 0
                    },
                    away: {
                        name: match.teams.away.name,
                        score: match.goals?.away || 0
                    }
                },
                date: new Date(match.fixture.date),
                competition: 'LaLiga',
                status: 'completed',
                sources: [{
                    name: sourceName,
                    lastChecked: new Date(),
                    verified: true
                }]
            };

            // Actualizar si existe, crear si no
            await MatchResult.findOneAndUpdate(
                {
                    'teams.home.name': matchData.teams.home.name,
                    'teams.away.name': matchData.teams.away.name,
                    date: matchData.date
                },
                {
                    $set: matchData,
                    $setOnInsert: { createdAt: new Date() },
                    $currentDate: { updatedAt: true }
                },
                { upsert: true, new: true }
            );
        } catch (error) {
            console.error('❌ Error guardando en base de datos:', error);
        }
    }
}

export default new ResultsService();