// Input de formulario del tema (el de login/register: surfaceAlt,
// radio 10, borde, texto claro). Todas las props de TextInput se
// propagan tal cual.
import React from "react";
import {
  StyleProp,
  StyleSheet,
  TextInput,
  TextInputProps,
  TextStyle,
} from "react-native";
import { colors } from "@/src/theme";

type Props = TextInputProps & {
  style?: StyleProp<TextStyle>;
};

export function FormInput({ style, ...rest }: Props) {
  return <TextInput style={[styles.input, style]} {...rest} />;
}

const styles = StyleSheet.create({
  input: {
    backgroundColor: colors.surfaceAlt,
    borderRadius: 10,
    padding: 15,
    marginBottom: 15,
    borderWidth: 1,
    borderColor: colors.border,
    fontSize: 16,
    color: colors.text,
  },
});
