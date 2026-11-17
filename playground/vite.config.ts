import { fileURLToPath } from "node:url";
import { defineConfig, loadEnv } from "vite";
import react from "@vitejs/plugin-react";
import type { Connect, ProxyOptions } from "vite";

const acessoLocal: Connect.NextHandleFunction = (req, res, next) => {
  const host = req.headers.host ?? "";
  const origem = req.headers.origin;
  if (/^(localhost|127\.0\.0\.1)(:\d+)?$/i.test(host) && (!origem || origem === `http://${host}`)) {
    next();
    return;
  }
  res.statusCode = 403;
  res.end("Acesse o playground pelo endereço local.");
};

const hermesProxy = (apiKey: string): ProxyOptions => ({
  target: "http://127.0.0.1:8642",
  changeOrigin: true,
  rewrite: (path) => path.replace(/^\/hermes/, ""),
  timeout: 900_000,
  proxyTimeout: 900_000,
  configure: (proxy) => {
    proxy.on("proxyReq", (proxyReq) => {
      // O Hermes recusa Origin fora da lista (127.0.0.1 dá 403). Quem chama é o proxy.
      proxyReq.removeHeader("origin");
      proxyReq.setHeader("Authorization", `Bearer ${apiKey}`);
    });
  },
});

// Carrinho, saldo e perfil do negócio: serviço `api` do compose (porta 8700 no loopback do host).
const cartProxy: ProxyOptions = { target: "http://127.0.0.1:8700" };

export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, fileURLToPath(new URL("..", import.meta.url)), "");
  const apiKey = env.API_SERVER_KEY || "sabor-da-maria-chave-local";
  return {
    plugins: [react(), {
      name: "acesso-local",
      configureServer: ({ middlewares }) => { middlewares.use(acessoLocal); },
      configurePreviewServer: ({ middlewares }) => { middlewares.use(acessoLocal); },
    }],
    server: {
      host: "127.0.0.1",
      port: 3000,
      strictPort: true,
      proxy: { "/hermes": hermesProxy(apiKey), "/api": cartProxy },
    },
    preview: {
      host: "127.0.0.1",
      port: 3000,
      strictPort: true,
      proxy: { "/hermes": hermesProxy(apiKey), "/api": cartProxy },
    },
  };
});
