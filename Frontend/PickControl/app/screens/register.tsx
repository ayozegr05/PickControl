import React from 'react';
import { View, Text, TextInput, TouchableOpacity, StyleSheet, ScrollView, Alert } from 'react-native';
import { Formik } from 'formik';
import * as Yup from 'yup';
import BottomBar from '../components/bottom-bar';
import TopBar from '../components/top-bar';
import { useRouter } from 'expo-router';
import { useAuth } from '../context/AuthContext';

const RegisterSchema = Yup.object().shape({
  name: Yup.string()
    .required('El nombre es requerido'),
  email: Yup.string()
    .email('Email inválido')
    .required('El email es requerido'),
  password: Yup.string()
    .min(6, '¡La contraseña es muy corta!')
    .required('La contraseña es requerida'),
  confirmPassword: Yup.string()
    .oneOf([Yup.ref('password')], 'Las contraseñas deben coincidir')
    .required('Confirma tu contraseña'),
});

const RegisterScreen = () => {
  const router = useRouter();
  const { login } = useAuth();

  const handleRegister = async (values: any) => {
    try {
      const response = await fetch(`${process.env.EXPO_PUBLIC_API_BASE_URL}/register`, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
        },
        body: JSON.stringify({
          name: values.name,
          email: values.email,
          password: values.password,
        }),
      });

      const data = await response.json();

      if (!response.ok) {
        throw new Error(data.message || 'Error en el registro');
      }

      // Guardar el token recibido
      await login(data.token);
      
      // Mostrar mensaje de éxito
      Alert.alert(
        "Registro Exitoso",
        "Tu cuenta ha sido creada correctamente",
        [
          { 
            text: "OK", 
            onPress: () => router.push('/screens/login')
          }
        ]
      );

    } catch (error: any) {
      Alert.alert(
        "Error",
        error.message || "Hubo un problema al registrar tu cuenta"
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
            <Text style={styles.title}>Crear Cuenta</Text>
            <Text style={styles.subtitle}>Únete a nuestra comunidad</Text>
          </View>

          <Formik
            initialValues={{
              name: '',
              email: '',
              password: '',
              confirmPassword: '',
            }}
            validationSchema={RegisterSchema}
            onSubmit={handleRegister}
          >
            {({ handleChange, handleSubmit, values, errors, touched, isSubmitting }) => (
              <View style={styles.form}>
                <View style={styles.inputContainer}>
                  <TextInput
                    style={styles.input}
                    onChangeText={handleChange('name')}
                    value={values.name}
                    placeholder="Nombre completo"
                    placeholderTextColor="#95a5a6"
                  />
                  {errors.name && touched.name && (
                    <Text style={styles.errorText}>{errors.name}</Text>
                  )}

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

                  <TextInput
                    style={styles.input}
                    onChangeText={handleChange('confirmPassword')}
                    value={values.confirmPassword}
                    placeholder="Confirmar contraseña"
                    placeholderTextColor="#95a5a6"
                    secureTextEntry
                  />
                  {errors.confirmPassword && touched.confirmPassword && (
                    <Text style={styles.errorText}>{errors.confirmPassword}</Text>
                  )}
                </View>

                <TouchableOpacity
                  style={styles.button}
                  onPress={() => handleSubmit()}
                  disabled={isSubmitting}
                >
                  <Text style={styles.buttonText}>
                    {isSubmitting ? 'Registrando...' : 'Registrarse'}
                  </Text>
                </TouchableOpacity>

                <TouchableOpacity 
                  style={styles.loginLink}
                  onPress={() => router.push('/screens/login')}
                >
                  <Text style={styles.loginText}>
                    ¿Ya tienes una cuenta? 
                    <Text style={styles.loginLinkText}>  Inicia sesión</Text>
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
  loginLink: {
    marginTop: 30,
    alignItems: 'center',
  },
  loginText: {
    color: '#bbb',
    fontSize: 14,
  },
  loginLinkText: {
    color: '#ff9f1c', 
    fontWeight: 'bold',
    marginLeft: 5,
  },
});

export default RegisterScreen;