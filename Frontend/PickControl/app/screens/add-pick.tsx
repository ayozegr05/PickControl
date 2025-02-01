import React, { useState } from 'react';
import { View, Text, TextInput, StyleSheet, ScrollView, Alert, TouchableOpacity } from 'react-native';
import RNPickerSelect from 'react-native-picker-select'; // Nuevo dropdown
import BottomBar from "../components/bottom-bar"; // Asegúrate de que BottomBar esté correctamente importado

const AddPick = () => {
  // Estados para cada campo
  const [selectedInformante, setSelectedInformante] = useState('');
  const [selectedCasa, setSelectedCasa] = useState('');
  const [acierto, setAcierto] = useState('');
  const [cantidadApostada, setCantidadApostada] = useState('');
  const [apuesta, setApuesta] = useState('');
  const [tipoDeApuesta, setTipoDeApuesta] = useState('');
  const [cuota, setCuota] = useState(''); // Estado para el campo Cuota


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

  // Manejo del envío de la apuesta (con fetch para hacer el POST)
  const handleSubmit = async () => {
    // Preparamos los datos que vamos a enviar
    const datos = {
      Apuesta: apuesta,
      Informante: selectedInformante,
      TipoDeApuesta: tipoDeApuesta,
      Casa: selectedCasa,
      Acierto: acierto,
      CantidadApostada: cantidadApostada,
      Cuota: 1.5 
    };

    try {
      // Enviar el POST request a la API
      const response = await fetch(`${process.env.EXPO_PUBLIC_API_BASE_URL}/apuestas`, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json'
        },
        body: JSON.stringify(datos) // Convertimos los datos a formato JSON
      });

      const responseData = await response.json();

      if (response.status === 201) {
        Alert.alert('Apuesta enviada con éxito', responseData.message);
         // Restablecer los estados al valor inicial
        setSelectedInformante('');
        setSelectedCasa('');
        setAcierto('');
        setCantidadApostada('');
        setApuesta('');
        setTipoDeApuesta('');
        setCuota(''); // Restablecer la cuota
      } else {
        throw new Error('Hubo un error al enviar la apuesta');
      }
    } catch (error) {
      console.error('Error al hacer el POST:', error);
      Alert.alert('Error', 'Hubo un problema al enviar la apuesta');
    }
  };

  return (
    <View style={styles.container}>
      <Text style={styles.text}>Añadir Apuesta</Text>

      {/* ScrollView para asegurar que todo el formulario sea accesible en dispositivos más pequeños */}
      <ScrollView style={styles.scrollView}>
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
          <TextInput
            style={styles.input}
            placeholder="Tipo de Apuesta"
            value={tipoDeApuesta}
            onChangeText={setTipoDeApuesta}
          />

          {/* Informante Selector */}
          <RNPickerSelect
            onValueChange={(value) => setSelectedInformante(value)}
            items={informantes}
            placeholder={{ label: 'Selecciona Informante', value: '' }}
            style={pickerSelectStyles}
            value={selectedInformante}
          />

          {/* Casa de Apuestas Selector */}
          <RNPickerSelect
            onValueChange={(value) => setSelectedCasa(value)}
            items={casas}
            placeholder={{ label: 'Selecciona Casa de Apuestas', value: '' }}
            style={pickerSelectStyles}
            value={selectedCasa}
          />

          {/* Acierto Selector */}
          <RNPickerSelect
            onValueChange={(value) => setAcierto(value)}
            items={aciertos}
            placeholder={{ label: 'Selecciona Acierto', value: '' }}
            style={pickerSelectStyles}
            value={acierto}
          />

          {/* Cantidad Apostada */}
          <TextInput
            style={styles.input}
            placeholder="Cantidad Apostada (€)"
            keyboardType="numeric"
            value={cantidadApostada}
            onChangeText={setCantidadApostada}
          />

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
    marginTop: 20,
    fontWeight: 'bold',
    color: 'white',
    marginBottom: 20,
  },
  scrollView: {
    width: '100%',
    flex: 1,
  },
  card: {
    width: '90%',  // Lo hacemos más pequeño para que se vea más centrado
    marginTop: 30,
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
