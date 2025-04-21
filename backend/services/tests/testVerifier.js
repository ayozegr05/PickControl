import betVerifier from '../betVerifier.js';
import resultsService from '../resultsService.js';
import mongoose from 'mongoose';
import dotenv from 'dotenv';

// Cargar variables de entorno
dotenv.config();

// Array de apuestas para probar
const testBets = [
    {
        text: `Este es mi pronóstico para hoy 

R. Madrid + Over 1  LA LIGA 

21:00  CUOTA 1.50   STAKE 4 

El Real Madrid tiene una sólida trayectoria como local en el Santiago Bernabéu, donde no ha perdido sus últimos enfrentamientos en LaLiga contra el Athletic Club. Además, cuenta con jugadores clave como Vinícius Jr. y Rodrygo, capaces de marcar la diferencia en momentos cruciales. Su necesidad de sumar puntos para luchar por el título refuerza su probabilidad de no perder este encuentro.

Por su parte, el Athletic Club suele plantear partidos intensos, lo que podría generar oportunidades de gol para ambos equipos. Sin embargo, el talento ofensivo del Real Madrid sugiere que habrá más de un gol en el partido. La combinación de calidad individual y presión asegura un enfrentamiento emocionante.`,
        date: '2024-04-20',
        sport: 'futbol'
    },
    {
        text: `Este es mi pronóstico para hoy 

Barcelona + Over 1  LA LIGA 

16:15  CUOTA 1.50   STAKE 4 

El FC Barcelona es favorito para ganar contra el Celta de Vigo...`,
        date: '2024-04-19',
        sport: 'futbol'
    },
    {
        text: `Este es mi pronóstico para hoy 

Más de 1,5 goles  LA LIGA 

21:00  CUOTA 1.50   STAKE 4 

El enfrentamiento entre Espanyol y Getafe promete ser un partido...`,
        date: '2024-04-18',
        sport: 'futbol'
    }
];

// Probar la verificación
async function testVerification() {
    console.log('\n Iniciando pruebas de verificación...\n');
    
    // Conectar a MongoDB directamente
    try {
        console.log(' Conectando a MongoDB...');
        await mongoose.connect(process.env.DB_CONNECTION, {
            serverSelectionTimeoutMS: 60000, // 60 segundos
            socketTimeoutMS: 45000,          // 45 segundos
            connectTimeoutMS: 60000,         // 60 segundos
            useNewUrlParser: true,
            useUnifiedTopology: true
        });
        console.log(' Conexión a MongoDB establecida');
    } catch (error) {
        console.error(' Error conectando a MongoDB:', error);
    }
    
    let results = [];
    
    for (const bet of testBets) {
        console.log(`\n Probando apuesta del ${bet.date}:`);
        
        try {
            // Primero parsear la apuesta
            const parsedBet = betVerifier.parseFootballBet(bet.text);
            
            // Solo hacer scraping si tenemos equipos
            if (parsedBet && parsedBet.teams.length > 0) {
                console.log(` Buscando partidos para: ${parsedBet.teams.join(' vs ')}`);
                const result = await betVerifier.verifyFootballBet({...bet, parsedBet});
                console.log(' Resultado:', result);
                results.push({ date: bet.date, status: 'success', data: result });
            } else {
                console.log(' No se pudieron identificar los equipos en la apuesta');
                results.push({ date: bet.date, status: 'error', error: 'No se identificaron equipos' });
            }
        } catch (error) {
            console.log(' Error:', error.message);
            results.push({ date: bet.date, status: 'error', error: error.message });
        }
        console.log('-'.repeat(50));
    }

    // Mostrar resumen final
    console.log('\n Resumen de resultados:');
    for (const result of results) {
        const icon = result.status === 'success' ? '' : '';
        console.log(`${icon} ${result.date}: ${result.status === 'success' ? 'OK' : result.error}`);
    }
}

// Ejecutar los tests
console.log(' Iniciando tests...\n');

// Solo ejecutar verificación
testVerification()
    .then(() => console.log('\n Tests completados'))
    .catch(error => console.error(' Error en los tests:', error));