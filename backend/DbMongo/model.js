import mongoose from 'mongoose';
import bcrypt from 'bcryptjs';

// Definir el esquema de la apuesta
const apuestaSchema = new mongoose.Schema({
    Apuesta: { 
        type: String, 
        required: true 
    },
    Informante: { 
        type: String, 
        required: true 
    },
    TipoDeApuesta: { 
        type: String, 
        required: true 
    },
    Acierto: { 
        type: String,  
        required: true
    },
    Casa: { 
        type: String, 
        required: true 
    },
    CantidadApostada: {
        type: Number, 
        required: true, 
        default: 0  
    },
    Cuota: {  
        type: Number, 
        required: true, 
        default: 1  
    },
    Fecha: {
        type: Date,
        default: Date.now
    },
    usuario: {
        type: mongoose.Schema.Types.ObjectId,
        ref: 'Users',
        required: true
    }
}, {
    timestamps: true // Añade createdAt y updatedAt
});

// Definir el esquema del usuario
const userSchema = new mongoose.Schema({
    name: {
        type: String,
        required: [true, 'El nombre es requerido'],
        trim: true
    },
    email: {
        type: String,
        required: [true, 'El email es requerido'],
        unique: true,
        trim: true,
        lowercase: true,
        match: [/^\w+([\.-]?\w+)*@\w+([\.-]?\w+)*(\.\w{2,3})+$/, 'Por favor ingresa un email válido']
    },
    password: {
        type: String,
        required: [true, 'La contraseña es requerida'],
        minlength: [6, 'La contraseña debe tener al menos 6 caracteres']
    },
    role: {
        type: String,
        enum: ['user', 'admin'],
        default: 'user'
    },
    createdAt: {
        type: Date,
        default: Date.now
    },
    lastLogin: {
        type: Date
    },
    isActive: {
        type: Boolean,
        default: true
    }
}, {
    timestamps: true, // Añade createdAt y updatedAt
    toJSON: { virtuals: true }, // Incluye virtuals cuando conviertes a JSON
    toObject: { virtuals: true } // Incluye virtuals cuando conviertes a objeto
});

// Middleware para hashear la contraseña antes de guardar
userSchema.pre('save', async function(next) {
    if (!this.isModified('password')) return next();
    
    try {
        const salt = await bcrypt.genSalt(10);
        this.password = await bcrypt.hash(this.password, salt);
        next();
    } catch (error) {
        next(error);
    }
});

// Método para comparar contraseñas
userSchema.methods.comparePassword = async function(candidatePassword) {
    try {
        return await bcrypt.compare(candidatePassword, this.password);
    } catch (error) {
        throw error;
    }
};

// Virtual para obtener todas las apuestas del usuario
userSchema.virtual('apuestas', {
    ref: 'Picks',
    localField: '_id',
    foreignField: 'usuario'
});

// Crear los modelos
const Pick = mongoose.model('Picks', apuestaSchema, 'Picks');
const User = mongoose.model('Users', userSchema);

export { Pick, User };
