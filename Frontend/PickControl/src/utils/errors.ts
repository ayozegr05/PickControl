// Convierte cualquier valor capturado en un catch (unknown) a un
// mensaje de UI seguro. Centraliza el patrón
// `err instanceof Error ? err.message : "..."` repetido por la app.

export function toErrorMessage(err: unknown, fallback: string): string {
  if (err instanceof Error && err.message) return err.message;
  return fallback;
}
