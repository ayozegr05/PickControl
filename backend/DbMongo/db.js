import mongoose from 'mongoose';

// Función para conectar a MongoDB
export async function connectDB() {
    try {
        await mongoose.connect(process.env.DB_CONNECTION);
        console.log('Connected successfully to MongoDB');
    } catch (error) {
        console.error('Error connecting to MongoDB:', error);
    }
}



