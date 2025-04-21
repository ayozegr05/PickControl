import mongoose from 'mongoose';

// Función para conectar a MongoDB
export async function connectDB() {
    try {
        // Opciones de conexión con timeouts aumentados
        const options = {
            serverSelectionTimeoutMS: 60000, // 60 segundos (por defecto 30s)
            socketTimeoutMS: 45000,          // 45 segundos
            connectTimeoutMS: 60000,         // 60 segundos
            useNewUrlParser: true,
            useUnifiedTopology: true
        };
        
        await mongoose.connect(process.env.DB_CONNECTION, options);
        console.log('Connected successfully to MongoDB');
    } catch (error) {
        console.error('Error connecting to MongoDB:', error);
    }
}
