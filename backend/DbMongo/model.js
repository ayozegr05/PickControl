import mongoose from 'mongoose';

// Definir el esquema de la apuesta
const apuestaSchema = new mongoose.Schema({
    Apuesta: { 
        type: String, required: true 
    },
    Informante: { 
        type: String, required: true 
    },
    TipoDeApuesta: { 
        type: String, required: true 
    },
    Acierto: { 
        type: String,  required: true
    },
    Casa: { 
        type: String, required: true 
    },
    CantidadApostada: {
        type: Number, required: true, default: 0  // Cantidad apostada, tipo número
    },
    Cuota: {  // Nuevo campo para la cuota
        type: Number, required: true, default: 1  // Por defecto es 1 si no se proporciona
    }
});

// Crear el modelo 'Pick' con el esquema definido y el nombre explícito de la colección
const Pick = mongoose.model('Picks', apuestaSchema, 'Picks');

export default Pick;
