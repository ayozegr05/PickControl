import { Pick } from '../DbMongo/model.js';
import resultsService from './resultsService.js';
import { findOfficialTeamName } from './teams.js';

class BetVerifier {
    parseFootballBet(betText) {
        console.log('\n🔍 Analizando apuesta:', betText);
        
        const bet = {
            type: null,
            teams: [],
            divisions: new Set(),
            value: null,
            time: null
        };
    
        // Extraer tipo de apuesta y valor
        if (betText.includes('Over') || betText.includes('Más de')) {
            bet.type = 'over';
            const match = betText.match(/(?:Over|Más de) (\d+(?:\.\d+)?)/);
            bet.value = match ? parseFloat(match[1]) : null;
        }
    
        // Buscar equipos en todo el texto
        // Primero dividimos el texto en fragmentos más pequeños
        const fragments = [
            ...betText.split('\n'),           // Buscar en cada línea
            betText,                          // Buscar en todo el texto completo
            ...betText.split(/(?:vs|contra|entre|y)\s+/)  // Buscar alrededor de palabras clave
        ];

        // Buscar equipos en cada fragmento
        for (const fragment of fragments) {
            const team = findOfficialTeamName(fragment);
            if (team && !bet.teams.includes(team.name)) {
                bet.teams.push(team.name);
                bet.divisions.add(team.division);
            }
        }
    
        // Extraer hora
        const timeLine = betText.split('\n').find(line => line.includes('⌚️'));
        if (timeLine) {
            const timeMatch = timeLine.match(/⌚️\s*(\d{2}:\d{2})/);
            bet.time = timeMatch ? timeMatch[1] : null;
        }
    
        if (bet.teams.length > 0) {
            console.log('🏟️ Equipos encontrados:', bet.teams);
            console.log('📊 Divisiones:', Array.from(bet.divisions));
        }
    
        console.log('✅ Apuesta parseada:', bet);
        return bet;
    }

    async verifyFootballBet(bet) {
        const { parsedBet, date } = bet;
        
        if (!parsedBet || !parsedBet.teams || parsedBet.teams.length === 0) {
            throw new Error('No encontrado');
        }

        // Buscar el partido usando el servicio multi-fuente
        const { response: matches, error } = await resultsService.findMatch(parsedBet.teams, date);
        
        if (error || !matches || matches.length === 0) {
            throw new Error('Partido no encontrado');
        }

        return matches[0];
    }
}

export default new BetVerifier();