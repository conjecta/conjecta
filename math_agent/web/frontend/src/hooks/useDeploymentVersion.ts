import { useEffect, useRef, useState } from 'react';

const VERSION_URL = '/api/version';

export function useDeploymentVersion(pollMs = 30_000) {
  const [updateAvailable, setUpdateAvailable] = useState(false);
  const initialVersion = useRef<string | null>(null);

  useEffect(() => {
    let cancelled = false;

    const fetchVersion = async () => {
      try {
        const resp = await fetch(VERSION_URL);
        if (!resp.ok) return;
        const data = (await resp.json()) as { version?: string };
        const version = data.version;
        if (!version) return;

        if (initialVersion.current === null) {
          initialVersion.current = version;
        } else if (initialVersion.current !== version) {
          if (!cancelled) setUpdateAvailable(true);
        }
      } catch {
        // Best-effort polling; ignore transient network errors.
      }
    };

    fetchVersion();
    const id = window.setInterval(fetchVersion, pollMs);

    return () => {
      cancelled = true;
      window.clearInterval(id);
    };
  }, [pollMs]);

  return updateAvailable;
}
