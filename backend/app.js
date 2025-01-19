import express from 'express';
import morgan from 'morgan';
import router from './DbMongo/routes.js';
// import router from './Api/routes/index.js';
import { fileURLToPath } from 'url';
import { dirname, join } from 'path';  
import { connectDB } from './DbMongo/db.js';
import dotenv from 'dotenv';

// import exphbs from 'express-handlebars'
// import path from 'path';

dotenv.config();

const __filename = fileURLToPath(import.meta.url);
const __dirname = dirname(__filename);

const app = express();

// app.set('views', path.join(__dirname, 'views'));
// app.engine('.hbs', exphbs.engine({ 
//     defaultLayout: 'main',
//     extname: '.hbs',
// }));
// app.set('view engine', '.hbs');

app.use(morgan('dev'));
app.use(express.json());
app.use(express.urlencoded({ extended: false }))
app.use(router);

// Llama a la función para conectar a MongoDB
connectDB();

// app.use(express.static(join(__dirname, 'public'))); 

export default app;
