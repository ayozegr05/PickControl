// Núcleo compartido de los hooks de datos (Fase B): ciclo
// loading→data/error + reload() para el refetch que las pantallas
// ya hacen dentro de useFocusEffect. El fetcher llega memoizado por
// useCallback en el hook concreto — si cambia (p. ej. nuevo nombre en
// la ruta dinámica) se refetchea solo.
//
// `enabled=false` evita el fetch automático (p. ej. la landing sin
// sesión: pegarle a la API sería un 401 que dispara el logout
// global). reload() manual sigue funcionando para cuando la
// pantalla decida.
import { useCallback, useEffect, useRef, useState } from "react";

export type ApiData<T> = {
  data: T | null;
  loading: boolean;
  error: string | null;
  reload: () => Promise<void>;
  setData: React.Dispatch<React.SetStateAction<T | null>>;
};

export function useApiData<T>(
  fetcher: () => Promise<T>,
  enabled = true
): ApiData<T> {
  const [data, setData] = useState<T | null>(null);
  const [loading, setLoading] = useState(enabled);
  const [error, setError] = useState<string | null>(null);
  const mountedRef = useRef(true);

  const reload = useCallback(async () => {
    setLoading(true);
    try {
      const result = await fetcher();
      if (!mountedRef.current) return;
      setData(result);
      setError(null);
    } catch (err) {
      if (!mountedRef.current) return;
      setError(err instanceof Error ? err.message : "Error desconocido");
    } finally {
      if (mountedRef.current) setLoading(false);
    }
  }, [fetcher]);

  useEffect(() => {
    mountedRef.current = true;
    if (enabled) reload();
    return () => {
      mountedRef.current = false;
    };
  }, [reload, enabled]);

  return { data, loading, error, reload, setData };
}
