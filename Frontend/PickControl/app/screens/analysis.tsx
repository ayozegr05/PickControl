import React, { useState, useEffect, useRef } from 'react';
import { View, Text, StyleSheet, ScrollView, TouchableOpacity, TextInput } from 'react-native';
import { useRouter } from 'expo-router';
import TopBar from '../components/top-bar';
import BottomBar from '../components/bottom-bar';
import { MaterialIcons } from '@expo/vector-icons';
import LottieView from 'lottie-react-native';

interface Apuesta {
  Fecha: string;
  CantidadApostada: number;
  Cuota: number;
  Acierto: boolean;
  Informante: string;
}

export default function Analysis() {
  const [apuestas, setApuestas] = useState<Apuesta[]>([]);
  const [informantes, setInformantes] = useState<string[]>([]);
  const [selectedInformante, setSelectedInformante] = useState<string>('');
  const [inversionDiaria, setInversionDiaria] = useState('10');
  const [apuestasPorDia, setApuestasPorDia] = useState('1');
  const [loading, setLoading] = useState(true);
  const [dataLoaded, setDataLoaded] = useState(false);
  const loadingRef = useRef(null);

  const router = useRouter();

  useEffect(() => {
    fetchData();
  }, []);

  const fetchData = async () => {
    if (dataLoaded) return;
    
    try {
      setLoading(true);
      const response = await fetch(`${process.env.EXPO_PUBLIC_API_BASE_URL}/apuestas`, {
        method: 'GET',
        headers: {
          'Content-Type': 'application/json',
        },
      });
      const data = await response.json();
      console.log("Datos obtenidos:", data);

      if (data.picks) {
        // Organizar apuestas por informante
        const apuestasPorInformante = {};
        data.picks.forEach((pick) => {
          const { Informante, Acierto, CantidadApostada, Cuota, Fecha } = pick;
          // Solo incluir apuestas que no estén pendientes
          if (Acierto !== "Pending") {
            if (!apuestasPorInformante[Informante]) {
              apuestasPorInformante[Informante] = [];
            }
            apuestasPorInformante[Informante].push({ 
              Acierto: Acierto === "True", 
              CantidadApostada, 
              Cuota, 
              Fecha,
              Informante 
            });
          }
        });

        const informantesList = Object.keys(apuestasPorInformante);
        setInformantes(informantesList);
        
        // Convertir a array plano para los cálculos, excluyendo apuestas pendientes
        const allApuestas = Object.values(apuestasPorInformante)
          .flat();
        
        setApuestas(allApuestas);
      }
    } catch (error) {
      console.error("Error al obtener las apuestas:", error);
      setApuestas([]);
      setInformantes([]);
    } finally {
      setDataLoaded(true);
      setTimeout(() => setLoading(false), 500); // Pequeño delay para suavizar la transición
    }
  };

  const calcularGanancia = (cantidad: number, cuota: number, acierto: boolean) => {
    return acierto ? cantidad * (cuota - 1) : -cantidad;
  };

  const calcularEstadisticas = (informante: string) => {
    // Filtrar apuestas del informante y excluir las pendientes
    const apuestasInformante = apuestas.filter(a => a.Informante === informante);
    const apuestasRecientes = apuestasInformante.slice(-30); // Último mes
    
    if (apuestasRecientes.length === 0) return null;

    const totalApuestas = apuestasRecientes.length;
    const aciertos = apuestasRecientes.filter(a => a.Acierto).length;
    const porcentajeAciertos = (aciertos / totalApuestas) * 100;
    
    // Calcular ROI
    const inversionTotal = apuestasRecientes.reduce((sum, a) => sum + a.CantidadApostada, 0);
    const gananciasTotal = apuestasRecientes.reduce((sum, a) => 
      sum + calcularGanancia(a.CantidadApostada, a.Cuota, a.Acierto), 0);
    const roi = ((gananciasTotal) / inversionTotal) * 100;

    // Calcular tendencia
    const apuestas2Semanas = apuestasRecientes.slice(-14);
    const apuestas2SemanasAnteriores = apuestasRecientes.slice(-28, -14);
    const roi2Semanas = calcularROI(apuestas2Semanas);
    const roiAnterior = calcularROI(apuestas2SemanasAnteriores);
    const tendencia = roi2Semanas - roiAnterior;

    return {
      porcentajeAciertos,
      roi,
      tendencia,
      apuestasDiarias: totalApuestas / 30,
      totalApuestas,
      aciertos
    };
  };

  const calcularROI = (apuestas: Apuesta[]) => {
    const inversion = apuestas.reduce((sum, a) => sum + a.CantidadApostada, 0);
    const ganancias = apuestas.reduce((sum, a) => 
      sum + calcularGanancia(a.CantidadApostada, a.Cuota, a.Acierto), 0);
    return inversion > 0 ? ((ganancias) / inversion) * 100 : 0;
  };

  const calcularProyeccion = (informante: string) => {
    // Filtrar apuestas del informante y excluir las pendientes
    const apuestasInformante = apuestas.filter(a => a.Informante === informante);
    const apuestasRecientes = apuestasInformante.slice(-30);
    
    // Calcular porcentaje de aciertos
    const totalApuestas = apuestasRecientes.length;
    const aciertos = apuestasRecientes.filter(a => a.Acierto).length;
    const porcentajeAciertos = totalApuestas > 0 ? (aciertos / totalApuestas) * 100 : 0;
    
    // Calcular cuota media
    const cuotaMedia = apuestasRecientes.reduce((sum, a) => sum + a.Cuota, 0) / totalApuestas;
    
    // Cálculos de proyección mensual
    const numApuestasDiarias = Number(apuestasPorDia) || 1;
    const inversionPorApuesta = Number(inversionDiaria) || 10;
    const diasMes = 30;
    
    // Inversión mensual total
    const inversionMensual = inversionPorApuesta * numApuestasDiarias * diasMes;
    
    // Ganancia proyectada considerando cuota media y % aciertos
    const apuestasMensuales = numApuestasDiarias * diasMes;
    const apuestasGanadoras = apuestasMensuales * (porcentajeAciertos / 100);
    const apuestasPerdidas = apuestasMensuales - apuestasGanadoras;
    
    const gananciaProyectada = (apuestasGanadoras * inversionPorApuesta * cuotaMedia) - 
                              (apuestasMensuales * inversionPorApuesta);

    return {
        inversionMensual,
        gananciaProyectada,
        porcentajeAciertos,
        cuotaMedia
    };
  };

  const calcularNivelRiesgo = (stats: any) => {
    if (stats.porcentajeAciertos > 65 && stats.roi > 15 && stats.tendencia >= 0) {
      return 'BAJO';
    } else if (stats.porcentajeAciertos > 50 && stats.roi > 5) {
      return 'MEDIO';
    } else {
      return 'ALTO';
    }
  };

  return (
    <View style={styles.container}>
      <TopBar />
      <ScrollView style={styles.scrollView}>
        <Text style={styles.title}>Análisis de Rentabilidad</Text>
        
        {/* Selector de inversión diaria */}
        <View style={styles.inputContainer}>
          <View style={styles.inputGroup}>
            <Text style={styles.label}>Inversión por apuesta (€):</Text>
            <TextInput
              style={styles.input}
              value={inversionDiaria}
              onChangeText={setInversionDiaria}
              keyboardType="numeric"
              placeholder="10"
              placeholderTextColor="#666"
            />
          </View>
          <View style={styles.inputGroup}>
            <Text style={styles.label}>Apuestas por día:</Text>
            <TextInput
              style={styles.input}
              value={apuestasPorDia}
              onChangeText={setApuestasPorDia}
              keyboardType="numeric"
              placeholder="1"
              placeholderTextColor="#666"
            />
          </View>
        </View>

        {loading ? (
          <View style={styles.loadingContainer}>
            <Text style={styles.loadingText}>Cargando datos...</Text>
            <LottieView
              ref={loadingRef}
              source={require('../../assets/animations/loading.json')}
              style={styles.loadingAnimation}
              autoPlay
              loop
            />
          </View>
        ) : informantes.length === 0 ? (
          <View style={styles.errorContainer}>
            <Text style={styles.errorText}>No se pudieron cargar los datos</Text>
            <TouchableOpacity 
              style={styles.retryButton}
              onPress={fetchData}
            >
              <Text style={styles.retryButtonText}>Reintentar</Text>
            </TouchableOpacity>
          </View>
        ) : (
          <View style={styles.informantesList}>
            {informantes.map((informante) => {
              const stats = calcularEstadisticas(informante);
              const proyeccion = stats ? calcularProyeccion(informante) : null;

              if (!stats || !proyeccion) return null;

              return (
                <View 
                  key={informante}
                  style={styles.informanteCard}
                >
                  <View style={styles.headerContainer}>
                    <Text style={styles.informanteName}>{informante}</Text>
                    <TouchableOpacity 
                      onPress={() => router.push(`dynamic-routes/${informante}`)}
                      style={styles.statsButton}
                    >
                      <View style={styles.statsButtonContent}>
                        <MaterialIcons name="query-stats" size={20} color="#4CAF50" />
                        <Text style={styles.statsButtonText}>Ver más</Text>
                      </View>
                    </TouchableOpacity>
                  </View>
                  
                  <View style={styles.statsGrid}>
                    <View style={styles.statItem}>
                      <Text style={styles.statLabel}>Aciertos</Text>
                      <Text style={styles.statValue}>{stats.porcentajeAciertos.toFixed(1)}%</Text>
                      <Text style={styles.statDetail}>({stats.aciertos}/{stats.totalApuestas})</Text>
                    </View>
                    
                    <View style={styles.statItem}>
                      <Text style={styles.statLabel}>ROI</Text>
                      <Text style={[
                        styles.statValue,
                        { color: stats.roi >= 0 ? '#4CAF50' : '#F44336' }
                      ]}>{stats.roi.toFixed(1)}%</Text>
                    </View>

                    <View style={styles.statItem}>
                      <Text style={styles.statLabel}>Tendencia</Text>
                      <Text style={[
                        styles.statValue,
                        { color: stats.tendencia >= 0 ? '#4CAF50' : '#F44336' }
                      ]}>{stats.tendencia >= 0 ? '↑' : '↓'}</Text>
                    </View>
                  </View>

                  <View style={styles.projectionContainer}>
                    <Text style={styles.projectionTitle}>
                      Proyección Mensual ({inversionDiaria}€/apuesta)
                    </Text>
                    <Text style={styles.projectionValue}>
                      Ganancia est.: {proyeccion.gananciaProyectada.toFixed(2)}€
                    </Text>
                    <Text style={styles.projectionDetail}>
                      Inversión req.: {proyeccion.inversionMensual.toFixed(2)}€
                    </Text>
                    <View style={[
                      styles.riskBadge,
                      { backgroundColor: 
                        calcularNivelRiesgo(stats) === 'BAJO' ? '#4CAF50' :
                        calcularNivelRiesgo(stats) === 'MEDIO' ? '#FFC107' : '#F44336'
                      }
                    ]}>
                      <Text style={styles.riskText}>
                        Riesgo: {calcularNivelRiesgo(stats)}
                      </Text>
                    </View>
                  </View>
                </View>
              );
            })}
          </View>
        )}
      </ScrollView>
      <BottomBar />
    </View>
  );
}

const styles = StyleSheet.create({
  container: {
    flex: 1,
    backgroundColor: 'black',
    paddingBottom: 60,
    paddingTop: 60
  },
  scrollView: {
    flex: 1,
    padding: 15,
  },
  title: {
    fontSize: 24,
    fontWeight: 'bold',
    color: 'orange',
    marginBottom: 20,
    textAlign: 'center'
  },
  inputContainer: {
    marginBottom: 20,
    backgroundColor: '#1E1E1E',
    padding: 15,
    borderRadius: 10,
    gap: 15,
  },
  inputGroup: {
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'space-between',
  },
  input: {
    backgroundColor: '#2C2C2C',
    color: '#FFFFFF',
    padding: 10,
    borderRadius: 5,
    width: 100,
    textAlign: 'right',
  },
  label: {
    color: '#FFFFFF',
    fontSize: 16,
    marginRight: 10,
  },
  informantesList: {
    gap: 15,
  },
  informanteCard: {
    backgroundColor: '#1E1E1E',
    borderRadius: 10,
    padding: 15,
    marginBottom: 15,
  },
  informanteName: {
    fontSize: 20,
    fontWeight: 'bold',
    color: '#FFFFFF',
  },
  headerContainer: {
    flexDirection: 'row',
    justifyContent: 'space-around',
    alignItems: 'center',
    marginBottom: 20,
  },
  statsButton: {
    backgroundColor: '#2A2A2A',
    borderRadius: 20,
    padding: 8,
    paddingHorizontal: 12,
    borderWidth: 1,
    borderColor: '#4CAF50',
  },
  statsButtonContent: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 6,
  },
  statsButtonText: {
    color: '#4CAF50',
    fontSize: 14,
    fontWeight: '500',
  },
  statsGrid: {
    flexDirection: 'row',
    justifyContent: 'space-between',
    marginBottom: 15,
  },
  statItem: {
    alignItems: 'center',
    flex: 1,
  },
  statLabel: {
    color: '#BBBBBB',
    fontSize: 14,
    marginBottom: 5,
  },
  statValue: {
    color: '#FFFFFF',
    fontSize: 18,
    fontWeight: 'bold',
  },
  statDetail: {
    color: '#888888',
    fontSize: 12,
    marginTop: 2,
  },
  projectionContainer: {
    backgroundColor: '#2A2A2A',
    borderRadius: 8,
    padding: 12,
    marginTop: 10,
  },
  projectionTitle: {
    color: '#FFFFFF',
    fontSize: 16,
    marginBottom: 8,
  },
  projectionValue: {
    color: '#4CAF50',
    fontSize: 15,
    marginBottom: 4,
  },
  projectionDetail: {
    color: '#BBBBBB',
    fontSize: 14,
    marginBottom: 8,
  },
  riskBadge: {
    alignSelf: 'flex-start',
    paddingHorizontal: 10,
    paddingVertical: 4,
    borderRadius: 12,
  },
  riskText: {
    color: '#FFFFFF',
    fontSize: 12,
    fontWeight: 'bold',
  },
  loadingContainer: {
    flex: 1,
    justifyContent: 'center',
    alignItems: 'center',
    height: '100%',
    minHeight: 500,
    gap: 10,
    marginTop: -60
  },
  loadingText: {
    color: '#FFFFFF',
    fontSize: 18,
    textAlign: 'left',
    marginBottom: -70,
    marginStart: 5
  },
  loadingAnimation: {
    width: 150,
    height: 150,
  },
  errorContainer: {
    flex: 1,
    justifyContent: 'center',
    alignItems: 'center',
    padding: 20,
  },
  errorText: {
    color: '#F44336',
    fontSize: 16,
    marginBottom: 15,
  },
  retryButton: {
    backgroundColor: '#2A2A2A',
    padding: 10,
    borderRadius: 5,
  },
  retryButtonText: {
    color: '#FFFFFF',
    fontSize: 14,
  },
});