// Setup global de Jest: mocks de módulos nativos que las pantallas
// importan transitivamente (client.ts → storage/secure.ts).

// Requerido por React/RNTL para que los updates asíncronos se
// procesen dentro de act() en lugar de ensuciar la consola.
(
  globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }
).IS_REACT_ACT_ENVIRONMENT = true;
jest.mock("@react-native-async-storage/async-storage", () =>
  require("@react-native-async-storage/async-storage/jest/async-storage-mock")
);
