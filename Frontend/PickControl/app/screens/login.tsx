import React, { useEffect, useState } from 'react';
import { View, Text, TextInput, TouchableOpacity, StyleSheet, Alert, ScrollView } from 'react-native';
import { Formik } from 'formik';
import * as Yup from 'yup';
import * as LocalAuthentication from 'expo-local-authentication';
import { MaterialCommunityIcons } from '@expo/vector-icons';
import AsyncStorage from '@react-native-async-storage/async-storage';
import BottomBar from '../components/bottom-bar';
import TopBar from '../components/top-bar';
import { useRouter } from 'expo-router';
import { useAuth } from '../context/AuthContext'; // Corregir la ruta de importación

const LoginSchema = Yup.object().shape({
  email: Yup.string()
    .email('Email inválido')
    .required('El email es requerido'),
  password: Yup.string()
    .required('La contraseña es requerida'),
});

const LoginScreen = () => {
  const router = useRouter();
  const { login } = useAuth(); // Obtener la función de login del contexto de autenticación
  const [isBiometricSupported, setIsBiometricSupported] = useState(false);
  const [savedCredentials, setSavedCredentials] = useState(null);

  useEffect(() => {
    checkBiometricSupport();
    checkSavedCredentials();
  }, []);

  const checkBiometricSupport = async () => {
    const compatible = await LocalAuthentication.hasHardwareAsync();
    setIsBiometricSupported(compatible);
  };

  const checkSavedCredentials = async () => {
    try {
      const credentials = await AsyncStorage.getItem('userCredentials');
      if (credentials) {
        setSavedCredentials(JSON.parse(credentials));
      }
    } catch (error) {
      console.error('Error checking saved credentials:', error);
    }
  };

  const handleBiometricAuth = async () => {
    try {
      if (!savedCredentials) {
        Alert.alert(
          "No hay credenciales guardadas",
          "Primero debes iniciar sesión normalmente para poder usar la huella dactilar"
        );
        return;
      }

      const result = await LocalAuthentication.authenticateAsync({
        promptMessage: 'Autenticación con huella dactilar',
        cancelLabel: 'Cancelar',
        disableDeviceFallback: true,
      });

      if (result.success) {
        // Usar las credenciales guardadas para hacer login
        const response = await fetch(`${process.env.EXPO_PUBLIC_API_BASE_URL}/login`, {
          method: 'POST',
          headers: {
            'Content-Type': 'application/json',
          },
          body: JSON.stringify(savedCredentials),
        });

        const data = await response.json();
        console.log("Usuario logueado: ", data.user.name);
        console.log("Datos del login: ", data);

        if (!response.ok) {
          throw new Error(data.message || 'Error al iniciar sesión');
        }

        await login(data.token, data.user.name);
        router.replace('/');
      }
    } catch (error) {
      console.error('Error en autenticación biométrica:', error);
      Alert.alert(
        "Error",
        "No se pudo completar la autenticación biométrica"
      );
    }
  };

  const handleLogin = async (values: any) => {
    try {
      const response = await fetch(`${process.env.EXPO_PUBLIC_API_BASE_URL}/login`, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
        },
        body: JSON.stringify({
          email: values.email,
          password: values.password,
        }),
      });

      const data = await response.json();
      console.log("Usuario logueado: ", data.user.name);

      if (response.ok) {
        // Guardar credenciales
        await AsyncStorage.setItem('userCredentials', JSON.stringify({
          email: values.email,
          password: values.password
        }));

        // Hacer login con token y nombre
        await login(data.token, data.user.name);
        router.replace('/');
      } else {
        throw new Error(data.message || 'Error al iniciar sesión');
      }
    } catch (error: any) {
      Alert.alert(
        "Error",
        error.message || "Credenciales inválidas"
      );
      console.error('Error:', error);
    }
  };

  return (
    <View style={styles.container}>
      <TopBar />
      <ScrollView>
        <View style={styles.formContainer}>
          <View style={styles.headerContainer}>
            <Text style={styles.title}>Bienvenido</Text>
            <Text style={styles.subtitle}>Inicia sesión para continuar</Text>
          </View>

          {isBiometricSupported && savedCredentials && (
            <TouchableOpacity 
              style={styles.biometricButton}
              onPress={handleBiometricAuth}
            >
              <MaterialCommunityIcons name="fingerprint" size={30} color="#ff9f1c" />
              <Text style={styles.biometricText}>Usar huella dactilar</Text>
            </TouchableOpacity>
          )}

          <Formik
            initialValues={{
              email: '',
              password: '',
            }}
            validationSchema={LoginSchema}
            onSubmit={handleLogin}
          >
            {({ handleChange, handleSubmit, values, errors, touched, isSubmitting }) => (
              <View style={styles.form}>
                <View style={styles.inputContainer}>
                  <TextInput
                    style={styles.input}
                    onChangeText={handleChange('email')}
                    value={values.email}
                    placeholder="Email"
                    placeholderTextColor="#95a5a6"
                    keyboardType="email-address"
                    autoCapitalize="none"
                  />
                  {errors.email && touched.email && (
                    <Text style={styles.errorText}>{errors.email}</Text>
                  )}

                  <TextInput
                    style={styles.input}
                    onChangeText={handleChange('password')}
                    value={values.password}
                    placeholder="Contraseña"
                    placeholderTextColor="#95a5a6"
                    secureTextEntry
                  />
                  {errors.password && touched.password && (
                    <Text style={styles.errorText}>{errors.password}</Text>
                  )}
                </View>

                <TouchableOpacity
                  style={styles.button}
                  onPress={() => handleSubmit()}
                  disabled={isSubmitting}
                >
                  <Text style={styles.buttonText}>
                    {isSubmitting ? 'Iniciando sesión...' : 'Iniciar Sesión'}
                  </Text>
                </TouchableOpacity>

                <TouchableOpacity 
                  style={styles.registerLink}
                  onPress={() => router.push('/screens/register')}
                >
                  <Text style={styles.registerText}>
                    ¿No tienes una cuenta? 
                    <Text style={styles.registerLinkText}>  Regístrate</Text>
                  </Text>
                </TouchableOpacity>
              </View>
            )}
          </Formik>
        </View>
      </ScrollView>
      <BottomBar />
    </View>
  );
};

const styles = StyleSheet.create({
  container: {
    flex: 1,
    backgroundColor: '#1a1a1a', 
  },
  formContainer: {
    flex: 1,
    justifyContent: 'center',
    paddingHorizontal: 20,
    backgroundColor: '#2d2d2d', 
    marginTop: 80, 
    marginHorizontal: 20, 
    borderRadius: 20,
    shadowColor: '#000',
    shadowOffset: {
      width: 0,
      height: 4,
    },
    shadowOpacity: 0.3,
    shadowRadius: 5.84,
    elevation: 5,
    paddingBottom: 40,
  },
  headerContainer: {
    alignItems: 'center',
    marginVertical: 40,
  },
  title: {
    fontSize: 28,
    fontWeight: 'bold',
    color: '#ff9f1c', 
    marginBottom: 10,
  },
  subtitle: {
    fontSize: 16,
    color: '#bbb',
    textAlign: 'center',
  },
  form: {
    width: '100%',
  },
  inputContainer: {
    marginBottom: 20,
  },
  input: {
    backgroundColor: '#3d3d3d',
    borderRadius: 10,
    padding: 15,
    marginBottom: 15,
    borderWidth: 1,
    borderColor: '#4d4d4d',
    fontSize: 16,
    color: '#fff',
  },
  errorText: {
    color: '#ff6b6b',
    fontSize: 14,
    marginTop: -12,
    marginBottom: 10,
    marginLeft: 5,
  },
  button: {
    backgroundColor: '#2ecc71', 
    padding: 15,
    borderRadius: 10,
    alignItems: 'center',
    marginTop: 20,
    shadowColor: '#000',
    shadowOffset: {
      width: 0,
      height: 2,
    },
    shadowOpacity: 0.25,
    shadowRadius: 3.84,
    elevation: 5,
  },
  buttonText: {
    color: '#fff',
    fontSize: 16,
    fontWeight: '600',
  },
  registerLink: {
    marginTop: 30,
    alignItems: 'center',
  },
  registerText: {
    color: '#bbb',
    fontSize: 14,
  },
  registerLinkText: {
    color: '#ff9f1c', 
    fontWeight: 'bold',
    marginLeft: 5,
  },
  biometricButton: {
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'center',
    backgroundColor: '#3d3d3d',
    padding: 15,
    borderRadius: 10,
    marginBottom: 20,
    borderWidth: 1,
    borderColor: '#ff9f1c',
  },
  biometricText: {
    color: '#ff9f1c',
    fontSize: 16,
    marginLeft: 10,
    fontWeight: '600',
  },
});

export default LoginScreen;