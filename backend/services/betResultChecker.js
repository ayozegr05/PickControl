import { MatchResult } from '../DbMongo/model.js';
import mongoose from 'mongoose';
import dotenv from 'dotenv';
import { findOfficialTeamName } from './teams.js';

// Cargar variables de entorno
dotenv.config();

class BetResultChecker {
    constructor() {
        this.dbConnected = false;
    }

    /**
     * Conecta a la base de datos MongoDB si aún no está conectado
     */
    async ensureDbConnection() {
        if (!this.dbConnected) {
            try {
                await mongoose.connect(process.env.DB_CONNECTION, {
                    serverSelectionTimeoutMS: 60000,
                    socketTimeoutMS: 45000,
                    connectTimeoutMS: 60000,
                });
                console.log('BetResultChecker: Conexión a MongoDB establecida');
                this.dbConnected = true;
            } catch (error) {
                console.error('BetResultChecker: Error conectando a MongoDB:', error);
                throw error;
            }
        }
    }

    /**
     * Verifica todas las apuestas pendientes y actualiza su estado
     */
    async checkAllPendingBets() {
        await this.ensureDbConnection();
        
        try {
            // Primero, consultar directamente la base de datos para ver todas las colecciones
            const collections = await mongoose.connection.db.listCollections().toArray();
            console.log('Colecciones disponibles:', collections.map(c => c.name));
            
            // Consultar la colección picks directamente para ver su estructura
            const db = mongoose.connection.db;
            
            // Buscar apuestas de fútbol pendientes en la colección "picks"
            const footballPicks = await db.collection('picks').find({
                sport: "futbol",
                result: null
            }).toArray();
            
            console.log(`Encontradas ${footballPicks.length} apuestas de fútbol pendientes en la colección "picks"`);
            
            let updatedCount = 0;
            
            for (const bet of footballPicks) {
                const result = await this.checkBetResultDirect(bet);
                if (result.updated) {
                    updatedCount++;
                }
            }
            
            console.log(`BetResultChecker: Actualizadas ${updatedCount} apuestas de fútbol`);
            return { total: footballPicks.length, updated: updatedCount };
            
        } catch (error) {
            console.error('BetResultChecker: Error verificando apuestas:', error);
            throw error;
        }
    }
    
    /**
     * Verifica si una apuesta específica fue acertada (para documentos de la colección "picks")
     * @param {Object} bet - La apuesta a verificar
     * @returns {Object} - Resultado de la verificación
     */
    async checkBetResultDirect(bet) {
        await this.ensureDbConnection();
        
        try {
            // Extraer información de la apuesta
            const betDate = bet.date ? new Date(bet.date) : null;
            
            if (!betDate) {
                console.log(`BetResultChecker: La apuesta ${bet._id} no tiene fecha`);
                return { updated: false, reason: 'no_date' };
            }
            
            // Extraer equipos del texto de la apuesta
            const teams = this.extractTeamsFromText(bet.text || '');
            
            if (!teams || teams.length === 0) {
                console.log(`BetResultChecker: No se encontraron equipos en la apuesta ${bet._id}`);
                return { updated: false, reason: 'no_teams' };
            }
            
            console.log(`BetResultChecker: Buscando partido para equipos: ${teams.join(', ')} en fecha ${betDate.toISOString().split('T')[0]}`);
            
            // Buscar partido correspondiente
            const startOfDay = new Date(betDate);
            startOfDay.setHours(0, 0, 0, 0);
            
            const endOfDay = new Date(betDate);
            endOfDay.setHours(23, 59, 59, 999);
            
            // Primero, buscar todos los partidos en la base de datos para depuración
            const db = mongoose.connection.db;
            const allMatchesInDb = await db.collection('matchresults').find({}).limit(10).toArray();
            
            console.log(`BetResultChecker: Muestra de partidos en la base de datos (primeros 10):`);
            allMatchesInDb.forEach(match => {
                console.log(`- ${match.teams.home.name} vs ${match.teams.away.name} (${new Date(match.date).toISOString().split('T')[0]})`);
            });
            
            // Buscar partido con los equipos específicos sin filtrar por fecha
            const matchByTeams = await db.collection('matchresults').findOne({
                $or: [
                    { 'teams.home.name': { $in: teams } },
                    { 'teams.away.name': { $in: teams } }
                ]
            });
            
            if (matchByTeams) {
                console.log(`BetResultChecker: Partido encontrado por equipos (sin filtro de fecha): ${matchByTeams.teams.home.name} vs ${matchByTeams.teams.away.name} (${new Date(matchByTeams.date).toISOString().split('T')[0]})`);
                
                // Usar este partido para el resto del proceso
                return this.processMatchResult(bet, matchByTeams);
            }
            
            // Si no encontramos nada, intentar con nombres alternativos
            const alternativeTeams = teams.map(team => {
                if (team === 'Barcelona') return 'FC Barcelona';
                if (team === 'Celta') return 'Celta de Vigo';
                return team;
            });
            
            console.log(`BetResultChecker: Intentando con nombres alternativos: ${alternativeTeams.join(', ')}`);
            
            const matchWithAlternativeNames = await db.collection('matchresults').findOne({
                $or: [
                    { 'teams.home.name': { $in: alternativeTeams } },
                    { 'teams.away.name': { $in: alternativeTeams } }
                ]
            });
            
            if (matchWithAlternativeNames) {
                console.log(`BetResultChecker: Partido encontrado con nombres alternativos: ${matchWithAlternativeNames.teams.home.name} vs ${matchWithAlternativeNames.teams.away.name} (${new Date(matchWithAlternativeNames.date).toISOString().split('T')[0]})`);
                
                // Usar este partido para el resto del proceso
                return this.processMatchResult(bet, matchWithAlternativeNames);
            }
            
            console.log(`BetResultChecker: No se encontró partido para la apuesta ${bet._id}`);
            return { updated: false, reason: 'no_match' };
            
        } catch (error) {
            console.error(`BetResultChecker: Error verificando apuesta ${bet._id}:`, error);
            return { updated: false, reason: 'error', error: error.message };
        }
    }
    
    /**
     * Extrae los nombres de equipos de un texto
     * @param {String} text - El texto de la apuesta
     * @returns {Array} - Lista de nombres de equipos
     */
    extractTeamsFromText(text) {
        const teams = [];
        
        try {
            // Buscar equipos en el texto de la apuesta
            const fragments = [
                ...text.split('\n'),
                text,
                ...text.split(/(?:vs|contra|entre|y)\s+/)
            ];
            
            for (const fragment of fragments) {
                const team = findOfficialTeamName(fragment);
                if (team && !teams.includes(team.name)) {
                    teams.push(team.name);
                }
            }
        } catch (error) {
            console.error('BetResultChecker: Error extrayendo equipos:', error);
        }
        
        return teams;
    }
    
    /**
     * Evalúa si una apuesta fue acertada basándose en el texto y el resultado del partido
     * @param {String} text - El texto de la apuesta
     * @param {Object} match - El resultado del partido
     * @returns {Object} - Resultado de la evaluación
     */
    evaluateBetFromText(text, match) {
        // Extraer tipo de apuesta
        let betType = null;
        let betValue = null;
        
        // Intentar extraer tipo y valor del texto de la apuesta
        if (text.includes('Over') || text.includes('Más de')) {
            betType = 'over';
            const match = text.match(/(?:Over|Más de) (\d+(?:\.\d+)?)/i);
            betValue = match ? parseFloat(match[1]) : null;
        }
        
        if (!betType || betValue === null) {
            return { success: false, reason: 'unknown_bet_type' };
        }
        
        // Calcular goles totales
        const totalGoals = match.teams.home.score + match.teams.away.score;
        
        // Evaluar según el tipo de apuesta
        switch (betType) {
            case 'over':
                return { 
                    success: totalGoals > betValue, 
                    reason: `Goles totales: ${totalGoals}, Apuesta: > ${betValue}`
                };
            // Añadir más tipos de apuestas según sea necesario
            default:
                return { success: false, reason: 'unknown_bet_type' };
        }
    }
    
    /**
     * Procesa el resultado del partido para la apuesta
     * @param {Object} bet - La apuesta
     * @param {Object} match - El partido
     * @returns {Object} - Resultado del procesamiento
     */
    async processMatchResult(bet, match) {
        // Determinar si la apuesta fue acertada
        const betResult = this.evaluateBetFromText(bet.text || '', match);
        
        // Actualizar la apuesta en la base de datos directamente
        const db = mongoose.connection.db;
        await db.collection('picks').updateOne(
            { _id: bet._id },
            { 
                $set: {
                    result: {
                        verified: true,
                        success: betResult.success,
                        matchId: match._id,
                        homeTeam: match.teams.home.name,
                        awayTeam: match.teams.away.name,
                        homeScore: match.teams.home.score,
                        awayScore: match.teams.away.score,
                        reason: betResult.reason
                    }
                }
            }
        );
        
        console.log(`BetResultChecker: Apuesta ${bet._id} actualizada - Acierto: ${betResult.success ? 'SI' : 'NO'}`);
        return { 
            updated: true, 
            success: betResult.success, 
            reason: betResult.reason,
            match: {
                homeTeam: match.teams.home.name,
                awayTeam: match.teams.away.name,
                homeScore: match.teams.home.score,
                awayScore: match.teams.away.score
            }
        };
    }
}

export default new BetResultChecker();