from telethon import TelegramClient, events
from dotenv import load_dotenv
import os
import asyncio
from pymongo import MongoClient
from datetime import datetime, timezone
import re

# Cargar variables de entorno
load_dotenv(os.path.join(os.path.dirname(__file__), '..', '.env'))

# Configuración de Telegram
api_id = int(os.getenv('TELEGRAM_API_ID'))
api_hash = os.getenv('TELEGRAM_API_HASH')
phone = os.getenv('TELEGRAM_PHONE')

# Configuración de MongoDB
mongo_uri = os.getenv('DB_CONNECTION')
client = MongoClient(mongo_uri)
db = client.dbdeprueba

# Configuración de canales
CHANNELS = {
    'tenis': {
        'id': -1002329040062,
        'name': 'PicksOnline Tenis'
    },
    'futbol': {
        'id': -1001125596067,  # Añadimos el -100 antes del ID como en el bot
        'name': 'Canal Fútbol'
    }
}

def is_bet_message(text, sport):
    keywords = ['Liga', 'Champions', 'Stake', 'Cuota', 'Pronóstico', '⚽'] if sport == 'futbol' else ['ATP', 'WTA', 'Stake', 'Cuota', '🎾']
    keyword_count = sum(1 for keyword in keywords if keyword.lower() in text.lower())
    return keyword_count >= 2

def parse_bet_message(text, sport):
    channel = CHANNELS[sport]
    tournament = re.search(r'(Liga|Champions|Copa|Premier)\s+([^\n]+)' if sport == 'futbol' else r'🇪🇦\s+(ATP|WTA)\s+([^\n]+)', text)
    player = re.search(r'⚽\s+([^\n]+)' if sport == 'futbol' else r'🎾\s+([^\n]+)', text)
    odds = re.search(r'Cuota\s+([\d.]+)', text)
    stake = re.search(r'Stake\s+(\d+)', text)
    
    return {
        'tournament': tournament.group(2) if tournament else None,
        'player': player.group(1) if player else None,
        'odds': float(odds.group(1)) if odds else None,
        'stake': int(stake.group(1)) if stake else None,
        'raw_text': text
    }

async def process_message(msg, sport, is_historical=False):
    if not msg.text:
        return
        
    print(f"\nMensaje encontrado en canal de {sport} ({msg.date}):")
    print(msg.text)
    
    if not is_bet_message(msg.text, sport):
        print("No es un mensaje de apuesta, ignorando...")
        return
        
    print(f"¡Detectada una apuesta de {sport}!")
    bet_info = parse_bet_message(msg.text, sport)
    
    # Guardar en MongoDB
    pick = {
        'channelId': str(msg.chat_id),
        'messageId': msg.id,
        'text': msg.text,
        'date': msg.date,
        'sport': sport,
        'processed': False,
        'createdAt': datetime.now(timezone.utc),
        'tournament': bet_info['tournament'],
        'player': bet_info['player'],
        'odds': bet_info['odds'],
        'stake': bet_info['stake'],
        'result': None
    }
    
    # Verificar si el mensaje ya existe
    existing = db.picks.find_one({
        'channelId': str(msg.chat_id),
        'messageId': msg.id
    })
    
    if not existing:
        db.picks.insert_one(pick)
        print("Apuesta guardada en la base de datos")
        print(f"Detalles: {bet_info}")
    else:
        print("Mensaje ya existe en la base de datos")

async def monitor_channel(client, channel_id, sport):
    if channel_id:
        try:
            print(f"\nObteniendo mensajes históricos del canal de {sport}...")
            messages = await client.get_messages(channel_id, limit=10)
            for msg in messages:
                await process_message(msg, sport, is_historical=True)
            
            print(f"Monitoreando nuevos mensajes del canal de {sport}...")
            @client.on(events.NewMessage(chats=[channel_id]))
            async def handler(event):
                await process_message(event.message, sport)
                
        except Exception as e:
            print(f"Error monitoreando canal de {sport}: {str(e)}")

async def main():
    try:
        print("Conectando a Telegram...")
        client = TelegramClient('history_session', api_id, api_hash)
        await client.start(phone)
        
        # Procesar canal de tenis
        print("\nObteniendo mensajes históricos del canal de tenis...")
        await monitor_channel(client, CHANNELS['tenis']['id'], 'tenis')
        
        # Procesar canal de fútbol
        print("\nObteniendo mensajes históricos del canal de fútbol...")
        await monitor_channel(client, CHANNELS['futbol']['id'], 'futbol')
        
        await client.run_until_disconnected()
        
    except Exception as e:
        print(f"Error: {str(e)}")
        
if __name__ == "__main__":
    asyncio.run(main())