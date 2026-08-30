/// <reference types="vite/client" />

// Typed environment variables. Vite exposes only `VITE_`-prefixed vars to the
// client, which is a security boundary rather than a naming convention:
// anything here is compiled into the bundle and visible to anyone who opens
// devtools, so a secret must never be given a VITE_ name.
interface ImportMetaEnv {
  readonly VITE_API_URL: string;
}

interface ImportMeta {
  readonly env: ImportMetaEnv;
}
