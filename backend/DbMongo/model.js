import mongoose from 'mongoose';

// Definir el esquema de la apuesta
const apuestaSchema = new mongoose.Schema({
    Apuesta: { 
        type: String, required: true 
    },
    Acierto: { 
        type: [String], required: true 
    }, // Un array de cadenas para los aciertos
    Casa: { 
        type: [String], required: true 
    }, // Un array de cadenas para las casas
});

// Crear el modelo 'Apuesta' con el esquema definido y el nombre explícito de la colección
const Pick = mongoose.model('Picks', apuestaSchema, 'Picks')

export default Pick;
