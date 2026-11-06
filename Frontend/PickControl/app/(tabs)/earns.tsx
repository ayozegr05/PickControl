import React, { useCallback, useEffect, useState, useRef } from "react";
import { View, Text, StyleSheet, ScrollView, Animated } from "react-native";
import { useSafeAreaInsets } from "react-native-safe-area-context";
import { useFocusEffect } from "expo-router";
import { Ionicons } from "@expo/vector-icons";
import LottieView from "lottie-react-native";
import TopBar from "@/src/components/top-bar";
import { ErrorState } from "@/src/components/inline-error";
import { usePicks } from "@/src/hooks/usePicks";
import {
  gananciasPor,
  totalGanancias as sumarGanancias,
} from "@/src/utils/picks";
import { colors } from "@/src/theme";

const GananciasPage = () => {
  const { apuestas, loading: fetching, error, reload } = usePicks();
  const [totalGanancias, setTotalGanancias] = useState(0);
  const [gananciasInformante, setGananciasInformante] = useState<
    Record<string, number>
  >({});
  const [gananciasBookmaker, setGananciasBookmaker] = useState<
    Record<string, number>
  >({});
  const [isLoading, setIsLoading] = useState(true);
  const [dataLoaded, setDataLoaded] = useState(false);
  const animatedValue = useRef(new Animated.Value(0)).current;
  const [animatedGanancias, setAnimatedGanancias] = useState("0.00");
  const rocketRef = useRef<LottieView>(null);
  const loseRef = useRef<LottieView>(null);
  const loadingRef = useRef<LottieView>(null);
  const insets = useSafeAreaInsets();

  // El hook fetchea al montar; cuando llegan los datos se agregan y
  // arranca la animación (equivalente al antiguo `dataLoaded` guard:
  // el proceso solo ocurre la primera vez).
  const processed = useRef(false);

  // La animación en curso: se para al desmontar. Antes el timer de
  // 1500ms quedaba vivo tras salir de la pestaña y su callback hacía
  // setState sobre el componente muerto (y en tests envenenaba el
  // render del componente siguiente).
  const currentAnim = useRef<Animated.CompositeAnimation | null>(null);
  useEffect(
    () => () => {
      currentAnim.current?.stop();
    },
    []
  );

  // Refetch al volver a la pestaña: antes `dataLoaded` bloqueaba el
  // refetch de por vida y los datos quedaban obsoletos tras añadir o
  // liquidar apuestas. El primer foco se salta porque la carga de
  // montaje ya la hace el hook.
  const firstFocus = useRef(true);
  useFocusEffect(
    useCallback(() => {
      if (firstFocus.current) {
        firstFocus.current = false;
        return;
      }
      processed.current = false; // reagregar y re-animar con datos frescos
      reload();
    }, [reload])
  );

  useEffect(() => {
    if (fetching || processed.current) return;
    processed.current = true;

    // Con error no hay animación ni agregación de un array vacío:
    // directo al ErrorState (evita el 0.00€ falso).
    if (error) {
      setIsLoading(false);
      return;
    }

    const ganancias = sumarGanancias(apuestas);
    setTotalGanancias(ganancias);
    setGananciasInformante(gananciasPor(apuestas, "informante"));
    setGananciasBookmaker(gananciasPor(apuestas, "casa"));
    setDataLoaded(true);

    // Iniciar la animación después de que los datos estén listos
    currentAnim.current = Animated.timing(animatedValue, {
      toValue: ganancias,
      duration: 1500,
      useNativeDriver: false,
    });
    currentAnim.current.start(() => {
      setIsLoading(false);
    });
  }, [fetching, error]);

  useEffect(() => {
    const listener = animatedValue.addListener(({ value }) => {
      setAnimatedGanancias(value.toFixed(2));
    });
    return () => {
      animatedValue.removeListener(listener);
      setIsLoading(true); // Resetear el estado de carga al desmontar
      setDataLoaded(false);
    };
  }, [animatedValue]);

  // No iniciar las animaciones hasta que los datos estén completamente cargados
  useEffect(() => {
    if (!isLoading && dataLoaded) {
      // Iniciar la animación de las ganancias
      animatedValue.setValue(0);
      currentAnim.current = Animated.timing(animatedValue, {
        toValue: totalGanancias,
        duration: 1500,
        useNativeDriver: false,
      });
      currentAnim.current.start(() => {
        // Una vez terminada la animación, reproducir la animación de rocket o lose
        if (totalGanancias > 0 && rocketRef.current) {
          rocketRef.current.play();
        } else if (totalGanancias <= 0 && loseRef.current) {
          loseRef.current.play();
        }
      });
    }
  }, [isLoading, dataLoaded, totalGanancias]);

  return (
    <View style={styles.container}>
      <TopBar />
      <View style={[styles.titleContainer, { marginTop: 80 + insets.top }]}>
        <Text style={styles.title}>Mis Ganancias</Text>
        <Ionicons
          name="wallet"
          size={35}
          color="brown"
          style={styles.titleIcon}
        />
      </View>

      <ScrollView
        style={styles.scrollView}
        contentContainerStyle={{ paddingBottom: 24 }}
      >
        {isLoading ? (
          <View style={styles.loadingContainer}>
            <Text style={styles.loadingText}>Cargando datos...</Text>
            <LottieView
              ref={loadingRef}
              source={require("../../assets/animations/loading.json")}
              style={styles.loadingAnimation}
              autoPlay
              loop
            />
          </View>
        ) : error ? (
          // Antes un fallo de red dejaba un 0.00€ falso presentado
          // como ganancia real — el peor dato posible para una app
          // de auditoría. Error explícito con reintento.
          <ErrorState onRetry={reload} />
        ) : (
          <>
            <Text style={styles.subtitle}>Tus ganancias totales son:</Text>

            <View style={styles.iconContainer}>
              <Animated.Text
                style={[
                  styles.gananciasText,
                  {
                    color: totalGanancias >= 0 ? colors.success : colors.danger,
                  },
                ]}
              >
                {animatedGanancias} €
              </Animated.Text>
              {totalGanancias > 0 ? (
                <View style={styles.rocketContainer}>
                  <LottieView
                    ref={rocketRef}
                    source={require("../../assets/animations/rocket.json")}
                    style={styles.lottieRocket}
                    autoPlay
                    loop
                  />
                </View>
              ) : (
                <View style={styles.loseContainer}>
                  <LottieView
                    ref={loseRef}
                    source={require("../../assets/animations/lose2.json")}
                    style={styles.lottieLose}
                    autoPlay
                    loop
                  />
                </View>
              )}
            </View>

            {/* Ganancias por Informante */}
            <Text style={styles.sectionTitle}>Por Informante:</Text>
            {Object.entries(gananciasInformante).map(
              ([informante, ganancia]) => (
                <View key={informante} style={styles.itemContainer}>
                  <Text style={styles.itemName}>{informante}</Text>
                  <Text
                    style={[
                      styles.itemValue,
                      { color: ganancia >= 0 ? colors.success : colors.danger },
                    ]}
                  >
                    {ganancia.toFixed(2)} €
                  </Text>
                </View>
              )
            )}

            {/* Ganancias por Casa de Apuestas */}
            <Text style={styles.sectionTitle}>Por Casa de Apuestas:</Text>
            {Object.entries(gananciasBookmaker).map(([bookmaker, ganancia]) => (
              <View key={bookmaker} style={styles.itemContainer}>
                <Text style={styles.itemName}>{bookmaker}</Text>
                <Text
                  style={[
                    styles.itemValue,
                    { color: ganancia >= 0 ? colors.success : colors.danger },
                  ]}
                >
                  {ganancia.toFixed(2)} €
                </Text>
              </View>
            ))}
          </>
        )}
      </ScrollView>
    </View>
  );
};

const styles = StyleSheet.create({
  container: {
    flex: 1,
    backgroundColor: colors.black,
  },
  scrollView: {
    flex: 1,
    paddingHorizontal: 20,
  },
  titleContainer: {
    flexDirection: "row",
    alignItems: "center",
    justifyContent: "center",
    marginTop: 80,
    marginBottom: 20,
  },
  title: {
    fontSize: 35,
    fontWeight: "bold",
    color: colors.primary,
    marginRight: 10,
  },
  titleIcon: {
    marginTop: 5,
  },
  subtitle: {
    fontSize: 20,
    color: colors.text,
    marginBottom: 20,
    fontWeight: "bold",
    marginTop: 20,
    textAlign: "center",
  },
  sectionTitle: {
    fontSize: 22,
    color: colors.primary,
    marginTop: 30,
    marginBottom: 15,
    fontWeight: "bold",
  },
  gananciasText: {
    fontSize: 32,
    fontWeight: "bold",
    marginTop: 20,
  },
  rocketIcon: {
    marginLeft: 20,
    marginTop: 10,
  },
  iconContainer: {
    flexDirection: "row",
    justifyContent: "center",
    alignItems: "center",
    marginBottom: 20,
  },
  itemContainer: {
    flexDirection: "row",
    justifyContent: "space-between",
    alignItems: "center",
    backgroundColor: colors.background,
    padding: 15,
    borderRadius: 10,
    marginBottom: 10,
  },
  itemName: {
    color: colors.text,
    fontSize: 16,
    fontWeight: "500",
  },
  itemValue: {
    fontSize: 18,
    fontWeight: "bold",
  },
  rocketContainer: {
    position: "relative",
    marginLeft: 20,
    width: 100,
    height: 100,
  },
  loseContainer: {
    position: "relative",
    marginLeft: 20,
    width: 100,
    height: 100,
  },
  lottieRocket: {
    width: "100%",
    height: "100%",
  },
  lottieLose: {
    width: "100%",
    height: "100%",
  },
  loadingContainer: {
    flex: 1,
    justifyContent: "center",
    alignItems: "center",
    height: "100%",
    minHeight: 500,
    gap: 10,
    marginTop: 40,
  },
  loadingText: {
    color: colors.text,
    fontSize: 18,
    textAlign: "left",
    marginBottom: -70,
    marginStart: 5,
  },
  loadingAnimation: {
    width: 150,
    height: 150,
  },
});

export default GananciasPage;
