// Núcleo compartido de los hooks de datos (Fase B): ciclo
// loading→data/error + reload() para el refetch que las pantallas
// ya hacen dentro de useFocusEffect. El fetcher llega memoizado por
// useCallback en el hook concreto — si cambia (p. ej. nuevo nombre en
// la ruta dinámica) se refetchea solo.
import { useCallback, useEffect, useRef, useState } from "react";

export type ApiData<T> = {
  data: T | null;
  loading: boolean;
  error: string | null;
  reload: () => Promise<void>;
};

export function useApiData<T>(fetcher: () => Promise<T>): ApiData<T> {
  const [data, setData] = useState<T | null>(null);
  const [loading, setLoading] = useState(true);
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
    reload();
    return () => {
      mountedRef.current = false;
    };
  }, [reload]);

  return { data, loading, error, reload };
}
