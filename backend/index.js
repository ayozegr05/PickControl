import app from "./app.js";
import { initBot } from './telegramBot.js';

// Iniciar el bot de Telegram
initBot().catch(console.error);

app.listen(3000, () => {
    console.log('Server listening on port 3000');
});