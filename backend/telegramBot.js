import TelegramBot from 'node-telegram-bot-api';
import dotenv from 'dotenv';
import { Pick, TelegramChannel } from './DbMongo/model.js';

dotenv.config();

// Configurar el bot con webhooks
const token = process.env.TELEGRAM_BOT2_TOKEN; // Usar el nuevo token
const bot = new TelegramBot(token, { 
    polling: {
        interval: 300,
        autoStart: true,
        params: {
            timeout: 10
        }
    }
});

// Configurar el perfil del bot para parecer más humano
const setupBotProfile = async () => {
    try {
        // Configurar descripción
        await bot.setMyDescription("Analista deportivo | Seguimiento de eventos y estadísticas ");
        
        // Configurar información corta
        await bot.setMyShortDescription("Análisis deportivo ");
        
        // Configurar comandos disponibles
        await bot.setMyCommands([
            { command: '/start', description: 'Iniciar seguimiento' },
            { command: '/stats', description: 'Ver estadísticas' }
        ]);
        
        console.log('Perfil del bot configurado correctamente');
    } catch (error) {
        console.error('Error configurando perfil del bot:', error);
    }
};

// Función para extraer información de apuestas de tenis
const parseTennisMessage = (text) => {
    const data = {
        Apuesta: text,
        TipoDeApuesta: 'tenis',
        Acierto: 'pendiente',
        Casa: 'pendiente',
        CantidadApostada: 0,
        Cuota: 1
    };

    try {
        // Extraer cuota
        const cuotaMatch = text.match(/Cuota (\d+\.?\d*)/i);
        if (cuotaMatch) {
            data.Cuota = parseFloat(cuotaMatch[1]);
        }

        // Extraer stake
        const stakeMatch = text.match(/Stake (\d+)/i);
        if (stakeMatch) {
            data.CantidadApostada = parseInt(stakeMatch[1]);
        }

        // Extraer casa de apuestas
        const casaMatch = text.match(/\((.*?)\)/);
        if (casaMatch) {
            data.Casa = casaMatch[1];
        }

        // Extraer la apuesta específica
        const apuestaMatch = text.match(/ (.*?)(?=\. Cuota|$)/);
        if (apuestaMatch) {
            data.Apuesta = apuestaMatch[1].trim();
        }
    } catch (error) {
        console.error('Error parsing tennis message:', error);
    }

    return data;
};

// Función para extraer información de apuestas de fútbol
const parseFootballMessage = (text) => {
    const data = {
        Apuesta: text,
        TipoDeApuesta: 'futbol',
        Acierto: 'pendiente',
        Casa: 'pendiente',
        CantidadApostada: 0,
        Cuota: 1
    };

    try {
        // Extraer cuota
        const cuotaMatch = text.match(/CUOTA (\d+\.?\d*)/i);
        if (cuotaMatch) {
            data.Cuota = parseFloat(cuotaMatch[1]);
        }

        // Extraer stake
        const stakeMatch = text.match(/STAKE (\d+)/i);
        if (stakeMatch) {
            data.CantidadApostada = parseInt(stakeMatch[1]);
        }

        // Extraer la apuesta específica
        const apuestaMatch = text.match(/ (.*?)(?=|$)/);
        if (apuestaMatch) {
            data.Apuesta = apuestaMatch[1].trim();
        }
    } catch (error) {
        console.error('Error parsing football message:', error);
    }

    return data;
};

// Función para procesar mensajes de apuestas
const processMessage = async (msg, sport) => {
    try {
        const messageText = msg.text;
        console.log(`Procesando mensaje de ${sport}:`, messageText);
        
        let parsedData;
        if (sport === 'tenis') {
            parsedData = parseTennisMessage(messageText);
        } else if (sport === 'futbol') {
            parsedData = parseFootballMessage(messageText);
        } else {
            parsedData = {
                Apuesta: messageText,
                TipoDeApuesta: sport,
                Acierto: 'pendiente',
                Casa: 'pendiente',
                CantidadApostada: 0,
                Cuota: 1
            };
        }

        const apuesta = new Pick({
            ...parsedData,
            Informante: msg.chat.title || 'Canal Telegram',
            source: 'telegram',
            channelId: msg.chat.id.toString(),
            messageId: msg.message_id.toString()
        });

        await apuesta.save();
        console.log('Apuesta guardada:', apuesta);
    } catch (error) {
        console.error('Error procesando mensaje:', error);
    }
};

// Configuración inicial de canales
const INITIAL_CHANNELS = [
    {
        name: 'PicksOnline Tenis',
        channelId: '-1002329040062',
        username: 'picksonline_tenis',
        sport: 'tenis'
    },
    {
        name: 'Canal Fútbol',
        channelId: '-1001125596067', 
        inviteLink: 'https://t.me/+w6wNMXJ3uXkyYThk',
        sport: 'futbol'
    }
];

// Función para unirse a un canal
const joinChannel = async (channel) => {
    try {
        console.log(`Intentando unirse al canal: ${channel.name}`);
        
        let chatId = channel.channelId;
        let inviteLink = channel.inviteLink;
        let username = channel.username;
        
        // Si tenemos un enlace de invitación, intentar unirse primero
        if (inviteLink) {
            try {
                console.log(`Intentando unirse usando enlace: ${inviteLink}`);
                await bot.getChat(inviteLink);
                console.log('Solicitud de unión enviada al canal');
            } catch (joinError) {
                console.log(`Info: No se pudo unir al canal: ${joinError.message}`);
            }
        }
        
        // Intentar obtener información del canal
        try {
            // Intentar diferentes formas de acceder al canal
            let chat;
            if (chatId) {
                chat = await bot.getChat(chatId);
            } else if (username) {
                chat = await bot.getChat('@' + username);
            } else if (inviteLink) {
                chat = await bot.getChat(inviteLink);
            }
            
            if (!chat) {
                throw new Error('No se pudo obtener información del canal');
            }
            
            console.log('Información del canal obtenida:', chat);
            
            // Actualizar información del canal en la base de datos
            const channelDoc = await TelegramChannel.findOneAndUpdate(
                { name: channel.name },
                {
                    $set: {
                        channelId: chat.id.toString(),
                        name: chat.title || channel.name,
                        username: chat.username,
                        inviteLink: inviteLink,
                        sport: channel.sport,
                        active: true,
                        lastChecked: new Date()
                    }
                },
                { upsert: true, new: true }
            );
            
            console.log('Canal actualizado en la base de datos:', channelDoc);
            return chat;
            
        } catch (error) {
            console.log(`Info: No se pudo obtener información del canal: ${error.message}`);
            
            // Si no podemos obtener la info, guardar con datos básicos
            const channelDoc = await TelegramChannel.findOneAndUpdate(
                { name: channel.name },
                {
                    $set: {
                        channelId: `pending_${Date.now()}`, // Hacemos el channelId único
                        name: channel.name,
                        inviteLink: inviteLink,
                        sport: channel.sport,
                        active: true,
                        lastChecked: new Date()
                    }
                },
                { upsert: true, new: true }
            );
            
            console.log('Canal guardado con información básica:', channelDoc);
            return null;
        }
    } catch (error) {
        console.error(`Error procesando canal: ${error.message}`);
        return null;
    }
};

// Función para obtener mensajes históricos
const getChannelHistory = async (chatId) => {
    try {
        console.log(`Obteniendo historial del canal ${chatId}...`);
        
        // Obtener los últimos mensajes usando getChat
        const chat = await bot.getChat(chatId);
        console.log('Chat info:', chat);
        
        // Intentar obtener los últimos mensajes usando getHistory
        try {
            const messages = await bot.getChatHistory(chatId, {
                limit: 10  // Obtener los últimos 10 mensajes
            });
            
            if (messages && messages.length > 0) {
                console.log(`Se encontraron ${messages.length} mensajes históricos`);
                
                // Procesar cada mensaje
                for (const message of messages) {
                    try {
                        console.log('Mensaje histórico encontrado:', message.text);
                        const channel = await TelegramChannel.findOne({ 
                            channelId: chatId 
                        });
                        
                        if (channel) {
                            await processMessage(message, channel.sport);
                        }
                    } catch (error) {
                        console.error('Error procesando mensaje histórico:', error);
                    }
                }
            } else {
                console.log('No se encontraron mensajes históricos. Esperando nuevos mensajes...');
            }
        } catch (error) {
            console.log('Intentando método alternativo para obtener mensajes...');
            try {
                // Método alternativo usando getHistory
                const history = await bot.getHistory(chatId, {
                    limit: 10
                });
                
                if (history && history.length > 0) {
                    console.log(`Se encontraron ${history.length} mensajes usando método alternativo`);
                    for (const message of history) {
                        console.log('Mensaje histórico encontrado:', message.text);
                        const channel = await TelegramChannel.findOne({ 
                            channelId: chatId 
                        });
                        
                        if (channel) {
                            await processMessage(message, channel.sport);
                        }
                    }
                }
            } catch (historyError) {
                console.log('No se pudieron obtener mensajes históricos. El bot solo procesará nuevos mensajes.');
            }
        }
    } catch (error) {
        console.error('Error obteniendo historial:', error);
    }
};

// Función para inicializar canales
const initializeChannels = async () => {
    for (const channelInfo of INITIAL_CHANNELS) {
        await joinChannel(channelInfo);
    }
};

// Función para iniciar el bot
export const initBot = async () => {
    try {
        console.log('Iniciando bot de Telegram...');
        await setupBotProfile();

        // Detener cualquier polling anterior y limpiar webhooks
        try {
            await bot.stopPolling();
            await bot.deleteWebHook();
        } catch (error) {
            console.log('Error limpiando estado previo del bot:', error.message);
        }

        // Configurar el bot con polling simple
        const options = {
            polling: true
        };

        // Reiniciar el bot con las nuevas opciones
        bot.polling = false;
        await new Promise(resolve => setTimeout(resolve, 1000));
        bot.polling = options.polling;

        // Mostrar información del bot
        const botInfo = await bot.getMe();
        console.log('Información del bot:', botInfo);
        
        // Inicializar canales
        await initializeChannels();

        // Escuchar nuevos mensajes
        bot.on('channel_post', async (msg) => {
            console.log('Nuevo mensaje recibido:', JSON.stringify(msg, null, 2));
            try {
                const channel = await TelegramChannel.findOne({ 
                    channelId: msg.chat.id.toString() 
                });
                
                if (channel) {
                    console.log(`Procesando mensaje del canal ${channel.name}`);
                    await processMessage(msg, channel.sport);
                }
            } catch (error) {
                console.error('Error procesando mensaje del canal:', error);
            }
        });

        console.log('Bot de Telegram iniciado correctamente');
    } catch (error) {
        console.error('Error al iniciar el bot:', error);
        // Intentar reiniciar el polling en caso de error
        bot.polling = true;
    }
};