import React from "react";
import { colors } from "@/src/theme";
import { Screen } from "@/src/components/screen";
import { ScreenTitle, ScreenSubtitle } from "@/src/components/typography";
import { FormInput } from "@/src/components/form-input";
import {
  View,
  Text,
  TouchableOpacity,
  StyleSheet,
  ScrollView,
  Alert,
  Platform,
  KeyboardAvoidingView,
} from "react-native";
import { useSafeAreaInsets } from "react-native-safe-area-context";
import { Formik } from "formik";
import * as Yup from "yup";
import TopBar from "@/src/components/top-bar";
import { useRouter } from "expo-router";
import { useAuth } from "@/src/context/AuthContext";
import { register } from "@/src/api/auth.api";
import { ApiError } from "@/src/api/client";
import { RegisterPayload } from "@/src/types/user.types";

type RegisterFormValues = RegisterPayload & { confirmPassword: string };

const RegisterSchema = Yup.object().shape({
  name: Yup.string().required("El nombre es requerido"),
  email: Yup.string().email("Email inválido").required("El email es requerido"),
  password: Yup.string()
    .min(6, "¡La contraseña es muy corta!")
    .required("La contraseña es requerida"),
  confirmPassword: Yup.string()
    .oneOf([Yup.ref("password")], "Las contraseñas deben coincidir")
    .required("Confirma tu contraseña"),
});

const RegisterScreen = () => {
  const router = useRouter();
  const { login } = useAuth();
  const insets = useSafeAreaInsets();

  const handleRegister = async (values: RegisterFormValues) => {
    try {
      const data = await register({
        name: values.name,
        email: values.email,
        password: values.password,
      });

      // Guardar el token y el nombre recibidos
      await login(data.token, data.user.name, data.user.role);

      // Mostrar mensaje de éxito
      Alert.alert(
        "Registro Exitoso",
        "Tu cuenta ha sido creada correctamente",
        [
          {
            text: "OK",
            onPress: () => router.replace("/"),
          },
        ]
      );
    } catch (error: unknown) {
      Alert.alert(
        "Error",
        error instanceof ApiError
          ? error.message
          : "Hubo un problema al registrar tu cuenta"
      );
      console.error("Error:", error);
    }
  };

  return (
    <Screen>
      <TopBar />
      <KeyboardAvoidingView
        style={styles.flex}
        behavior={Platform.OS === "ios" ? "padding" : undefined}
        keyboardVerticalOffset={Platform.OS === "ios" ? insets.top + 60 : 0}
      >
        <ScrollView
          contentContainerStyle={{ paddingBottom: 24 }}
          keyboardShouldPersistTaps="handled"
        >
          <View style={[styles.formContainer, { marginTop: 80 + insets.top }]}>
            <View style={styles.headerContainer}>
              <ScreenTitle size={28} style={styles.title}>
                Crear Cuenta
              </ScreenTitle>
              <ScreenSubtitle size={16} style={styles.subtitle}>
                Únete a nuestra comunidad
              </ScreenSubtitle>
            </View>

            <Formik
              initialValues={{
                name: "",
                email: "",
                password: "",
                confirmPassword: "",
              }}
              validationSchema={RegisterSchema}
              onSubmit={handleRegister}
            >
              {({
                handleChange,
                handleSubmit,
                values,
                errors,
                touched,
                isSubmitting,
              }) => (
                <View style={styles.form}>
                  <View style={styles.inputContainer}>
                    <FormInput
                      onChangeText={handleChange("name")}
                      value={values.name}
                      placeholder="Nombre completo"
                      placeholderTextColor={colors.textMuted}
                    />
                    {errors.name && touched.name && (
                      <Text style={styles.errorText}>{errors.name}</Text>
                    )}

                    <FormInput
                      onChangeText={handleChange("email")}
                      value={values.email}
                      placeholder="Email"
                      placeholderTextColor={colors.textMuted}
                      keyboardType="email-address"
                      autoCapitalize="none"
                    />
                    {errors.email && touched.email && (
                      <Text style={styles.errorText}>{errors.email}</Text>
                    )}

                    <FormInput
                      onChangeText={handleChange("password")}
                      value={values.password}
                      placeholder="Contraseña"
                      placeholderTextColor={colors.textMuted}
                      secureTextEntry
                    />
                    {errors.password && touched.password && (
                      <Text style={styles.errorText}>{errors.password}</Text>
                    )}

                    <FormInput
                      onChangeText={handleChange("confirmPassword")}
                      value={values.confirmPassword}
                      placeholder="Confirmar contraseña"
                      placeholderTextColor={colors.textMuted}
                      secureTextEntry
                    />
                    {errors.confirmPassword && touched.confirmPassword && (
                      <Text style={styles.errorText}>
                        {errors.confirmPassword}
                      </Text>
                    )}
                  </View>

                  <TouchableOpacity
                    style={styles.button}
                    onPress={() => handleSubmit()}
                    disabled={isSubmitting}
                  >
                    <Text style={styles.buttonText}>
                      {isSubmitting ? "Registrando..." : "Registrarse"}
                    </Text>
                  </TouchableOpacity>

                  <TouchableOpacity
                    style={styles.loginLink}
                    onPress={() => router.push("/login")}
                  >
                    <Text style={styles.loginText}>
                      ¿Ya tienes una cuenta?
                      <Text style={styles.loginLinkText}> Inicia sesión</Text>
                    </Text>
                  </TouchableOpacity>
                </View>
              )}
            </Formik>
          </View>
        </ScrollView>
      </KeyboardAvoidingView>
    </Screen>
  );
};

const styles = StyleSheet.create({
  flex: {
    flex: 1,
  },
  formContainer: {
    flex: 1,
    justifyContent: "center",
    paddingHorizontal: 20,
    backgroundColor: colors.surface,
    marginTop: 80,
    marginHorizontal: 20,
    borderRadius: 20,
    shadowColor: colors.black,
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
    alignItems: "center",
    marginVertical: 40,
  },
  title: {
    marginBottom: 10,
  },
  subtitle: {
    textAlign: "center",
  },
  form: {
    width: "100%",
  },
  inputContainer: {
    marginBottom: 20,
  },
  errorText: {
    color: colors.danger,
    fontSize: 14,
    marginTop: -12,
    marginBottom: 10,
    marginLeft: 5,
  },
  button: {
    backgroundColor: colors.primary,
    padding: 15,
    borderRadius: 10,
    alignItems: "center",
    marginTop: 20,
    shadowColor: colors.black,
    shadowOffset: {
      width: 0,
      height: 2,
    },
    shadowOpacity: 0.25,
    shadowRadius: 3.84,
    elevation: 5,
  },
  buttonText: {
    color: colors.onPrimary,
    fontSize: 16,
    fontWeight: "600",
  },
  loginLink: {
    marginTop: 30,
    alignItems: "center",
  },
  loginText: {
    color: colors.textSecondary,
    fontSize: 14,
  },
  loginLinkText: {
    color: colors.primary,
    fontWeight: "bold",
    marginLeft: 5,
  },
});

export default RegisterScreen;
