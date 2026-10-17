# README media

These captures use the repository's actual HTML, CSS, JavaScript and terminal component. API responses and terminal output are synthetic. The fixture blocks every external browser request and never starts an agent, reads production Console state or submits a real credential.

The instance label says **Demo data**. The only entered key is `demo-key-not-a-real-credential`; the normal password field masks it. The Environment screenshot must be recaptured when its UI changes.

## Recreate

From the repository root:

```bash
npm ci
npx playwright install chromium
python3 -m venv /tmp/agent-console-media
/tmp/agent-console-media/bin/pip install Pillow==12.1.1
MEDIA_PYTHON=/tmp/agent-console-media/bin/python node scripts/capture-readme-media.mjs
```

The script starts and stops `tests/ui_server.py` on loopback port 4186. Set `AGCONSOLE_MEDIA_PORT` if that port is already in use. `PLAYWRIGHT_CHROMIUM_EXECUTABLE` can select an existing Chromium executable instead of downloading one. Browser launch and loopback binding must be allowed by the execution environment.

The script fails on JavaScript errors, missing assets, unhandled API routes, external requests, or GIFs of 2 MB or more. It checks that creating a session does not send its task, that explicit **Send + Enter** writes to the terminal, and that saving a variable clears the value field. This fixture checks the walkthrough, not real credential storage or upstream connectivity; the backend tests cover those behaviors.

## Files

| File | Shows |
| --- | --- |
| `work-overview.png` | Related demo work and an attention state |
| `new-session.png` | A filled session form |
| `session-tree.png` | Related sessions beside a terminal |
| `environment.png` | The existing form and a saved protected variable |
| `create-and-send.gif` | Create, review the draft, explicitly send |
| `save-protected-variable.gif` | Enter a masked demo value and save |

All captures use a 1280 × 1100 viewport, scale 1, light theme and reduced motion. GIFs use captured step frames with reading pauses and one shared palette; the UI pixels are not redrawn or retouched. README captions and static PNGs provide alternatives to the animations.
