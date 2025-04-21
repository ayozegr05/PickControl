import mongoose from 'mongoose';
import bcrypt from 'bcryptjs';

// Esquema para resultados de partidos
const matchResultSchema = new mongoose.Schema({
    teams: {
        home: {
            name: { type: String, required: true },
            score: { type: Number, default: 0 }
        },
        away: {
            name: { type: String, required: true },
            score: { type: Number, default: 0 }
        }
    },
    date: { type: Date, required: true },
    competition: { type: String, required: true },
    status: { type: String, enum: ['pending', 'completed', 'cancelled'], default: 'pending' },
    sources: [{
        name: { type: String, required: true },
        url: String,
        lastChecked: Date,
        verified: { type: Boolean, default: false }
    }],
    createdAt: { type: Date, default: Date.now },
    updatedAt: { type: Date, default: Date.now }
}, {
    timestamps: true
});

// Índices para búsqueda rápida
matchResultSchema.index({ 'teams.home.name': 1, 'teams.away.name': 1, date: 1 }, { unique: true });
matchResultSchema.index({ date: 1 });

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
    },
    source: {
        type: String,
        enum: ['manual', 'telegram'],
        default: 'manual'
    },
    channelId: {
        type: String,
        sparse: true
    },
    messageId: {
        type: String,
        sparse: true
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

// Definir el esquema del canal de Telegram
const telegramChannelSchema = new mongoose.Schema({
    channelId: {
        type: String,
        required: true,
        unique: true
    },
    name: {
        type: String,
        required: true
    },
    username: {
        type: String
    },
    type: {
        type: String
    },
    sport: {
        type: String
    },
    active: {
        type: Boolean,
        default: true
    },
    lastChecked: {
        type: Date,
        default: Date.now
    },
    inviteLink: {
        type: String
    }
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
const TelegramChannel = mongoose.model('TelegramChannel', telegramChannelSchema);
const MatchResult = mongoose.model('MatchResults', matchResultSchema);

export { Pick, User, TelegramChannel, MatchResult };
