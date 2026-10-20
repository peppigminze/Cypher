/// <reference types="vite/client" />

interface ImportMetaEnv {
  // Empty/unset = same origin (frontend served by the backend).
  readonly VITE_API_BASE_URL: string;
}

interface ImportMeta {
  readonly env: ImportMetaEnv;
}
