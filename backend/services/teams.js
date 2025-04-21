// Equipos de Primera División (LaLiga EA Sports 2023-24)
export const PRIMERA_DIVISION = {
    'Real Madrid': ['madrid', 'r. madrid', 'real madrid', 'rmadrid', 'real madrid cf'],
    'Barcelona': ['barça', 'barca', 'fc barcelona', 'fcbarcelona', 'barcelona'],
    'Atlético Madrid': ['atletico', 'atlético', 'atleti', 'atletico madrid', 'atlético madrid', 'atletico de madrid'],
    'Athletic Club': ['athletic', 'athletic bilbao', 'bilbao'],
    'Real Sociedad': ['sociedad', 'la real', 'real sociedad'],
    'Real Betis': ['betis', 'real betis'],
    'Valencia': ['valencia', 'valencia cf', 'vcf'],
    'Osasuna': ['osasuna', 'ca osasuna'],
    'Villarreal': ['villarreal', 'villareal', 'submarino amarillo'],
    'Girona': ['girona', 'girona fc'],
    'Rayo Vallecano': ['rayo', 'vallecano', 'rayo vallecano'],
    'Sevilla': ['sevilla', 'sevilla fc'],
    'Mallorca': ['mallorca', 'rcd mallorca'],
    'Getafe': ['getafe', 'getafe cf'],
    'Celta': ['celta', 'celta vigo', 'celta de vigo', 'rcelta'],
    'Alavés': ['alaves', 'alavés', 'deportivo alavés'],
    'Granada': ['granada', 'granada cf'],
    'Cádiz': ['cadiz', 'cádiz', 'cadiz cf'],
    'Las Palmas': ['las palmas', 'ud las palmas'],
    'Almería': ['almeria', 'almería', 'ud almería']
};

// Equipos de Segunda División (LaLiga Hypermotion 2023-24)
export const SEGUNDA_DIVISION = {
    'Espanyol': ['espanyol', 'rcd espanyol', 'español'],
    'Valladolid': ['valladolid', 'real valladolid', 'pucela'],
    'Sporting Gijón': ['sporting', 'gijón', 'sporting de gijón'],
    'Eibar': ['eibar', 'sd eibar'],
    'Levante': ['levante', 'levante ud'],
    'Tenerife': ['tenerife', 'cd tenerife'],
    'Racing Santander': ['racing', 'santander', 'racing santander'],
    'Real Oviedo': ['oviedo', 'real oviedo'],
    'Zaragoza': ['zaragoza', 'real zaragoza'],
    'Elche': ['elche', 'elche cf'],
    'Albacete': ['albacete', 'albacete bp'],
    'Burgos': ['burgos', 'burgos cf'],
    'Leganés': ['leganés', 'leganes', 'cd leganés'],
    'Huesca': ['huesca', 'sd huesca'],
    'Alcorcón': ['alcorcón', 'alcorcon', 'ad alcorcón'],
    'Mirandés': ['mirandés', 'mirandes', 'cd mirandés'],
    'Villarreal B': ['villarreal b', 'villarreal filial'],
    'Amorebieta': ['amorebieta', 'sd amorebieta'],
    'Eldense': ['eldense', 'cd eldense'],
    'Cartagena': ['cartagena', 'fc cartagena'],
    'Andorra': ['andorra', 'fc andorra'],
    'Racing Ferrol': ['racing ferrol', 'ferrol']
};

// Función para encontrar el nombre oficial del equipo
export function findOfficialTeamName(text) {
    text = text.toLowerCase();
    
    // Primero buscar en Primera División
    for (const [official, aliases] of Object.entries(PRIMERA_DIVISION)) {
        if (aliases.some(alias => text.includes(alias))) {
            return { name: official, division: 'primera' };
        }
    }
    
    // Si no se encuentra, buscar en Segunda División
    for (const [official, aliases] of Object.entries(SEGUNDA_DIVISION)) {
        if (aliases.some(alias => text.includes(alias))) {
            return { name: official, division: 'segunda' };
        }
    }
    
    return null;
}