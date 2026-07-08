# Exposing this app to the public internet (Tailscale Funnel)

`run.py` starts the API on `http://0.0.0.0:8000`, which by default is only
reachable on your local machine / LAN. [Tailscale](https://tailscale.com)
Funnel takes that local port and publishes it to the public internet over
HTTPS, without any router port-forwarding or manual TLS setup.

Free for personal use — no domain purchase required.

## One-time setup

1. Install Tailscale:

   ```powershell
   winget install --id Tailscale.Tailscale -e
   ```

2. Log in (opens a browser window to authenticate — GitHub, Google,
   Microsoft, or email all work):

   ```powershell
   tailscale up
   ```

   `tailscale status` should then show this machine with a `100.x.x.x`
   address once logged in.

3. Enable Funnel on your tailnet (one-time, per Tailscale account — not
   needed again for future ports/machines). Running the command below the
   first time will print a URL if it isn't enabled yet:

   ```powershell
   tailscale funnel --bg 8000
   ```

   If you see `Funnel is not enabled on your tailnet`, open the URL it
   prints (`https://login.tailscale.com/f/funnel?node=...`), enable it, then
   re-run the command above.

## Starting Funnel (make the app public)

With `run.py` already running (so something is actually listening on
`:8000`):

```powershell
tailscale funnel --bg 8000
```

`--bg` runs it in the background and returns immediately — you don't need
to keep a terminal open. Your public URL is shown by:

```powershell
tailscale funnel status
```

Example output:

```
# Funnel on:
#     - https://darshitwindos.tailb36c4a.ts.net

https://darshitwindos.tailb36c4a.ts.net (Funnel on)
|-- / proxy http://127.0.0.1:8000
```

That `https://<device>.<tailnet>.ts.net` URL is now the public address for
this app — anyone with the link can reach it, not just your other Tailscale
devices. HTTPS is handled automatically by Tailscale; you don't need a
certificate.

## Stopping Funnel (make the app private again)

```powershell
tailscale funnel off
```

The app keeps running and is still reachable locally (`localhost:8000`) and
from your own Tailscale devices — this only removes the public internet
route. Confirm it's off with `tailscale funnel status` (prints
`No serve config` when nothing is exposed).

## Checking status

```powershell
tailscale status          # is this device logged in / connected?
tailscale funnel status   # is anything currently public, and what's the URL?
```

## Notes

- The public URL (`https://<device>.<tailnet>.ts.net`) is stable as long as
  you don't rename the device or change tailnets — safe to bookmark/share.
- Funnel survives reboots as long as the Tailscale Windows service is
  running (it starts automatically), but `run.py` itself does **not**
  auto-start — if the app isn't running, the Funnel URL will fail to
  connect even though Funnel itself shows as "on".
- Before leaving this public for real, see the **Validation** and
  admin-auth sections in [README.md](README.md) — `/enqueue` has no
  authentication by default, and the admin password in `config.py` should
  be overridden via `.env` (`ADMIN_PASSWORD=...`) rather than left at its
  default once this is internet-reachable.
