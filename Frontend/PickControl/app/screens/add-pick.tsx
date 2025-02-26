import React, { useState } from 'react';
import { View, Text, TextInput, StyleSheet, ScrollView, Alert, TouchableOpacity } from 'react-native';
import RNPickerSelect from 'react-native-picker-select'; 
import BottomBar from "../components/bottom-bar"; 
import { useRouter } from "expo-router";
import AsyncStorage from '@react-native-async-storage/async-storage';
import TopBar from "../components/top-bar";

const AddPick = () => {
  // Estados para cada campo
  const [selectedInformante, setSelectedInformante] = useState('Dm7 Gratis');
  const [selectedCasa, setSelectedCasa] = useState('Bet365');
  const [acierto, setAcierto] = useState('Pending');
  const [cantidadApostada, setCantidadApostada] = useState('');
  const [apuesta, setApuesta] = useState('');
  const [tipoDeApuesta, setTipoDeApuesta] = useState('');
  const [primeraApuestaDoble, setPrimeraApuestaDoble] = useState('');
  const [segundaApuestaDoble, setSegundaApuestaDoble] = useState('');
  const [customPrimeraApuesta, setCustomPrimeraApuesta] = useState('');
  const [customSegundaApuesta, setCustomSegundaApuesta] = useState('');
  const [isDobleSelected, setIsDobleSelected] = useState(false);
  const [cuota, setCuota] = useState(''); 

  const router = useRouter();

  // Listas de opciones para los dropdowns
  const informantes = [
    { label: 'FunBet', value: 'FunBet' },
    { label: 'Mr Bet', value: 'Mr Bet' },
    { label: 'Dm7 Gratis', value: 'Dm7 Gratis' },
    { label: 'Dm7 AllSport', value: 'Dm7 AllSport' },
    { label: 'Ap. Diaria', value: 'Ap. Diaria' },
    { label: 'AllSportsPick', value: 'AllSportsPick' },
    { label: 'VipInsta', value: 'VipInsta' }
  ];

  const casas = [
    { label: 'Marathon', value: 'Marathon' },
    { label: 'BetWay', value: 'BetWay' },
    { label: 'BetFair', value: 'BetFair' },
    { label: 'Bet365', value: 'Bet365' }
  ];

  const aciertos = [
    { label: '✔️', value: 'True' },
    { label: '❌', value: 'False' },
    { label: '❓', value: 'Pending' }
  ];

  const tiposDeApuesta = [
    { label: 'Resultado', value: 'Resultado' },
    { label: 'Gol en ambas mitades', value: 'Gol en ambas mitades' },
    { label: 'Ambos equipos marcan', value: 'Ambos equipos marcan' },
    { label: 'Apuesta sin empate', value: 'Apuesta sin empate' },
    { label: 'Doble oportunidad', value: 'Doble oportunidad' },
    { label: '+1,5 goles', value: '+1,5 goles' },
    { label: '+2,5 goles', value: '+2,5 goles' },
    { label: '+3,5 goles', value: '+3,5 goles' },
    { label: '+4,5 goles', value: '+4,5 goles' },
    { label: '-4,5 goles', value: '-4,5 goles' },
    { label: '-3,5 goles', value: '-3,5 goles' },
    { label: '-2,5 goles', value: '-2,5 goles' },
    { label: 'Doble', value: 'Doble' },
    { label: 'Otro', value: 'Otro' },
  ];

  const tiposDeApuestaSinDoble = tiposDeApuesta.filter(tipo => tipo.value !== 'Doble');

  // Manejo del envío de la apuesta (con fetch para hacer el POST)
  const handleSubmit = async () => {
    // Preparamos los datos que vamos a enviar
    let finalTipoDeApuesta = tipoDeApuesta;
    
    if (tipoDeApuesta === 'Otro') {
      finalTipoDeApuesta = customPrimeraApuesta;
    } else if (tipoDeApuesta === 'Doble') {
      const primera = primeraApuestaDoble === 'Otro' ? customPrimeraApuesta : primeraApuestaDoble;
      const segunda = segundaApuestaDoble === 'Otro' ? customSegundaApuesta : segundaApuestaDoble;
      finalTipoDeApuesta = `Doble: ${primera} + ${segunda}`;
    }

    const datos = {
      Apuesta: apuesta,
      Informante: selectedInformante,
      TipoDeApuesta: finalTipoDeApuesta,
      Casa: selectedCasa,
      Acierto: acierto,
      CantidadApostada: cantidadApostada,
      Cuota: cuota 
    };

    try {
      // Obtener el token del almacenamiento local
      const token = await AsyncStorage.getItem('userToken');
      
      if (!token) {
        Alert.alert('Error', 'No has iniciado sesión');
        router.push("/login");
        return;
      }

      // Enviar el POST request a la API
      const response = await fetch(`${process.env.EXPO_PUBLIC_API_BASE_URL}/apuestas`, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          'Authorization': `Bearer ${token}`
        },
        body: JSON.stringify(datos)
      });

      const responseData = await response.json();

      if (response.status === 201) {
        console.log("Datos enviados: ", responseData);
        Alert.alert(
          'Apuesta enviada con éxito',
          responseData.message,
          [
              { text: "OK", onPress: () => router.push("/") } 
          ]
        );
         // Restablecer los estados al valor inicial
        setSelectedInformante('');
        setSelectedCasa('');
        setAcierto('');
        setCantidadApostada('');
        setApuesta('');
        setTipoDeApuesta('');
        setPrimeraApuestaDoble('');
        setSegundaApuestaDoble('');
        setCustomPrimeraApuesta('');
        setCustomSegundaApuesta('');
        setIsDobleSelected(false);
        setCuota(''); 
      } else {
        throw new Error(responseData.error || 'Hubo un error al enviar la apuesta');
      }
    } catch (error) {
      console.error('Error al hacer el POST:', error);
      Alert.alert('Error', error.message || 'Hubo un error al enviar la apuesta');
    }
  };

  return (
    <View style={styles.container}>
      <TopBar />

      {/* ScrollView para asegurar que todo el formulario sea accesible en dispositivos más pequeños */}
      <ScrollView style={styles.scrollView}>
      <Text style={styles.text}>Añade tu apuesta</Text>
        {/* Card que contiene el formulario */}
        <View style={styles.card}>
          {/* Campo Apuesta */}
          <TextInput
            style={styles.input}
            placeholder="Apuesta"
            value={apuesta}
            onChangeText={setApuesta}
          />

          {/* Campo Tipo de Apuesta */}
          <RNPickerSelect
            onValueChange={(value) => {
              setTipoDeApuesta(value);
              if (value === 'Doble') {
                setIsDobleSelected(true);
                setPrimeraApuestaDoble('');
                setSegundaApuestaDoble('');
                setCustomPrimeraApuesta('');
                setCustomSegundaApuesta('');
              } else {
                setIsDobleSelected(false);
                setPrimeraApuestaDoble('');
                setSegundaApuestaDoble('');
                setCustomPrimeraApuesta('');
                setCustomSegundaApuesta('');
              }
            }}
            items={tiposDeApuesta}
            style={pickerSelectStyles}
            value={tipoDeApuesta}
            placeholder={{ label: 'Selecciona tipo de apuesta', value: null }}
          />

          {tipoDeApuesta === 'Otro' && (
            <TextInput
              style={styles.input}
              placeholder="Escribe el tipo de apuesta"
              value={customPrimeraApuesta}
              onChangeText={setCustomPrimeraApuesta}
            />
          )}

          {isDobleSelected && (
            <>
              <Text style={[styles.label, { color: 'white', marginTop: 10 }]}>Primera apuesta:</Text>
              <RNPickerSelect
                onValueChange={setPrimeraApuestaDoble}
                items={tiposDeApuestaSinDoble}
                style={pickerSelectStyles}
                value={primeraApuestaDoble}
                placeholder={{ label: 'Selecciona primera apuesta', value: null }}
              />
              {primeraApuestaDoble === 'Otro' && (
                <TextInput
                  style={styles.input}
                  placeholder="Escribe la primera apuesta"
                  value={customPrimeraApuesta}
                  onChangeText={setCustomPrimeraApuesta}
                />
              )}

              <Text style={[styles.label, { color: 'white', marginTop: 10 }]}>Segunda apuesta:</Text>
              <RNPickerSelect
                onValueChange={setSegundaApuestaDoble}
                items={tiposDeApuestaSinDoble}
                style={pickerSelectStyles}
                value={segundaApuestaDoble}
                placeholder={{ label: 'Selecciona segunda apuesta', value: null }}
              />
              {segundaApuestaDoble === 'Otro' && (
                <TextInput
                  style={styles.input}
                  placeholder="Escribe la segunda apuesta"
                  value={customSegundaApuesta}
                  onChangeText={setCustomSegundaApuesta}
                />
              )}
            </>
          )}

          {/* Informante Selector */}
          <RNPickerSelect
            onValueChange={(value) => setSelectedInformante(value)}
            items={informantes}
            style={pickerSelectStyles}
            value={selectedInformante}
          />

          {/* Casa de Apuestas Selector */}
          <RNPickerSelect
            onValueChange={(value) => setSelectedCasa(value)}
            items={casas}
            style={pickerSelectStyles}
            value={selectedCasa}
          />

          {/* Acierto Selector */}
          <RNPickerSelect
            onValueChange={(value) => setAcierto(value)}
            items={aciertos}
            style={pickerSelectStyles}
            value={acierto}
          />

          {/* Cantidad Apostada */}
          <View style={styles.inputContainer}>
            <TextInput
              style={[styles.input, styles.inputWithPrefix]}
              placeholder="Cantidad Apostada"
              keyboardType="numeric"
              value={cantidadApostada}
              onChangeText={setCantidadApostada}
            />
            <Text style={styles.prefix}>€</Text>
          </View>

          <TextInput
            style={styles.input}
            placeholder="Cuota"
            keyboardType="numeric"
            value={cuota}
            onChangeText={(text) => setCuota(text)}
          />

          {/* Botón de Enviar Apuesta con TouchableOpacity */}
          <TouchableOpacity style={styles.button} onPress={handleSubmit}>
            <Text style={styles.buttonText}>Enviar Apuesta</Text>
          </TouchableOpacity>
        </View>
      </ScrollView>

      {/* Bottom Bar */}
      <BottomBar />
    </View>
  );
};

// Estilos para los componentes
const styles = StyleSheet.create({
  container: {
    flex: 1,
    justifyContent: 'flex-start',
    alignItems: 'center',
    paddingTop: 20, // Espacio adicional en la parte superior
    backgroundColor: 'black' // Fondo negro general
  },
  text: {
    fontSize: 32,
    marginTop:50,
    fontWeight: 'bold',
    color: 'white',
    marginBottom: 20,
    textAlign: 'center'
  },
  scrollView: {
    width: '100%',
    flex: 1,
  },
  card: {
    width: '90%',  // Lo hacemos más pequeño para que se vea más centrado
    marginTop: 0,
    maxWidth: 400,
    padding: 35,  // Aumentamos el padding dentro de la card
    borderRadius: 10,
    backgroundColor: '#2F4F4F', // Color verde oscuro para la card
    shadowColor: '#000',
    shadowOffset: { width: 0, height: 4 },  // Aumentamos el tamaño de la sombra
    shadowOpacity: 0.2,  // Aumentamos la opacidad para que se vea más fuerte
    shadowRadius: 8, // Aumentamos el radio para que la sombra sea más difusa
    elevation: 12, // Sombra para Android
    marginBottom: 60, // Espacio para la bottom bar
    marginHorizontal: '5%', // Para centrarse más en la pantalla
  },
  input: {
    height: 50,
    width: '100%',
    fontWeight: 'bold',
    borderColor: '#ccc',
    borderWidth: 1,
    marginVertical: 10,
    paddingLeft: 10,
    borderRadius: 5,
    fontSize: 18,  // Aumentamos el tamaño de la fuente
    backgroundColor: 'white',  // Fondo blanco para los inputs
  },
  button: {
    height: 50,
    marginTop: 20,
    backgroundColor: '#FF4500',
    justifyContent: 'center',
    alignItems: 'center',
    borderRadius: 5,
    marginVertical: 10,
  },
  buttonText: {
    color: 'white',
    fontWeight: 'bold',
    fontSize: 22, // Tamaño de fuente 18
  },
    // Contenedor para input con prefijo

  inputContainer: {
    flexDirection: 'row',
    alignItems: 'center',
    borderRadius: 5,
    marginVertical: 10,
    backgroundColor: 'white'
  },
  // Estilo para el texto del prefijo (símbolo de euro)
  prefix: {
    fontSize: 18,
    fontWeight: 'bold',
    paddingHorizontal: 10,
    paddingBottom: 8,
    color: 'black',
  },
  // Ajuste para el TextInput cuando se usa con prefijo
  inputWithPrefix: {
    flex: 1,
    height: 45,
    paddingLeft: 10,
    borderColor: 'white'
  },
  label: {
    fontSize: 16,
    marginBottom: 5,
    color: 'white',
  },
});

// Estilo específico para el RNPickerSelect
const pickerSelectStyles = StyleSheet.create({
  inputAndroid: {
    height: 55,  // Aumentamos la altura para evitar corte
    backgroundColor: 'white', // Fondo blanco
    borderRadius: 5,
    paddingLeft: 10,
    borderWidth: 1,
    borderColor: '#ccc',
    marginVertical: 10,
    fontSize: 18, // Aumentamos el tamaño de la fuente
  },
  inputIOS: {
    height: 55,  // Aumentamos la altura para evitar corte
    backgroundColor: 'white', // Fondo blanco
    borderRadius: 5,
    paddingLeft: 10,
    borderWidth: 1,
    borderColor: '#ccc',
    marginVertical: 10,
    fontSize: 16, // Aumentamos el tamaño de la fuente
  },
});

export default AddPick;
