import betResultChecker from '../betResultChecker.js';
import mongoose from 'mongoose';
import { Pick } from '../../DbMongo/model.js';
import dotenv from 'dotenv';

// Cargar variables de entorno
dotenv.config();

// Función para probar la verificación de apuestas
async function testBetChecker() {
    console.log('\n Iniciando pruebas del verificador de apuestas...\n');
    
    try {
        // Opción 1: Verificar todas las apuestas pendientes
        console.log(' Verificando todas las apuestas pendientes...');
        const result = await betResultChecker.checkAllPendingBets();
        console.log(` Total de apuestas pendientes: ${result.total}`);
        console.log(` Apuestas actualizadas: ${result.updated}`);
        
        // Opción 2: Verificar una apuesta específica (descomentar si quieres probar)
        /*
        const betId = 'ID_DE_LA_APUESTA'; // Reemplazar con un ID real
        const bet = await Pick.findById(betId);
        if (bet) {
            console.log('\n Verificando apuesta específica...');
            const betResult = await betResultChecker.checkBetResult(bet);
            console.log(' Resultado:', betResult);
        }
        */
        
    } catch (error) {
        console.error(' Error en las pruebas:', error);
    } finally {
        // Cerrar la conexión a MongoDB
        await mongoose.disconnect();
        console.log('\n Pruebas completadas');
    }
}

// Ejecutar las pruebas
testBetChecker()
    .catch(error => console.error(' Error general:', error));