// Guard centralizado de rutas (ISSUES: antes solo my-picks usaba
// Redirect y /screens/analysis + /dynamic-routes/[informante] eran
// navegables sin sesión por deep link).
//
// Lógica pura separada del componente para poder testear la matriz
// completa sin montar el árbol de navegación.

export type AuthGuardState = {
  isAuthenticated: boolean;
  isInitializing: boolean;
};

// Pantallas alcanzables sin sesión: la landing ((tabs)/index — el
// grupo (tabs) a secas también cae aquí) y el login/register, ahora
// rutas de primer nivel (antes bajo /screens/…).
const PUBLIC_ROUTES = new Set(["login", "register"]);

const isLanding = (segments: string[]): boolean =>
  segments[0] === "(tabs)" &&
  (segments[1] === undefined || segments[1] === "index");

// Devuelve la ruta a la que hay que redirigir, o null si la actual
// es válida para el estado de auth.
export function redirectTarget(
  segments: string[],
  { isAuthenticated, isInitializing }: AuthGuardState
): string | null {
  // Hidratando: nunca redirigir, la UI enseña spinner.
  if (isInitializing) return null;

  if (!isAuthenticated) {
    if (isLanding(segments) || PUBLIC_ROUTES.has(segments[0])) {
      return null;
    }
    return "/";
  }

  // Autenticado: login/register ya no tienen sentido → home.
  if (PUBLIC_ROUTES.has(segments[0])) {
    return "/";
  }
  return null;
}
