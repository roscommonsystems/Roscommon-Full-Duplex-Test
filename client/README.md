# Web client

React/Vite frontend for the demo. `provision.sh` builds it on the instance and
[`serve.py`](../serve.py) serves the resulting `dist/` from the same port it
proxies `/api` on, so in normal use there is nothing to do here by hand.

## Developing it locally

```bash
nvm use          # the version in .nvmrc
npm install
npm run dev      # http://localhost:5173
```

The dev server needs a backend for `/api` (models, status, the chat websocket).
Two ways to get one:

- Point the page straight at an instance: `http://localhost:5173/?worker_addr=<host:port>`.
- Or set `VITE_QUEUE_API_URL` in [`.env.local`](./.env.local) to a running
  `serve.py` and let Vite proxy `/api` there.

Mic access needs a secure context. `localhost` counts as one, so plain HTTP is
fine for dev — drop a `cert.pem`/`key.pem` pair in this directory only if you
need to reach the dev server from another machine, and Vite will serve HTTPS.

`npm run build` type-checks and writes `dist/`. `npm test` runs the vitest suite.

## License

Derived from Kyutai's moshi client, MIT — see [LICENSE](./LICENSE).
