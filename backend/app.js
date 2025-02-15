import express from 'express';
import morgan from 'morgan';
import router from './DbMongo/routes.js';
import { fileURLToPath } from 'url';
import { dirname, join } from 'path';  
import { connectDB } from './DbMongo/db.js';
import dotenv from 'dotenv';
import cors from 'cors';

dotenv.config();

const __filename = fileURLToPath(import.meta.url);
const __dirname = dirname(__filename);

const app = express();

app.use(morgan('dev'));
app.use(express.json());
app.use(express.urlencoded({ extended: false }));
app.use(cors()); // Reemplaza '*' por tu dominio en producción

// Rutas
app.use('/', router); // Cambiamos /api por / ya que todas las rutas están en el mismo archivo

app.get("/", (req, res) => {
    res.send("Servidor funcionando");
});

// Función para mantener el servidor activo
const keepAlive = () => {
    const serverUrl = process.env.SERVER_URL
    setInterval(async () => {
        try {
            const response = await fetch(`${serverUrl}/`);
            console.log('Keep-alive ping enviado:', new Date().toISOString());
        } catch (error) {
            console.error('Error en keep-alive ping:', error);
        }
    }, 14 * 60 * 1000); 
};

// Conectar a MongoDB
connectDB();

// Iniciar keep-alive si estamos en producción
if (process.env.NODE_ENV === 'production') {
    keepAlive();
}

export default app;
