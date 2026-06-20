const { app, BrowserWindow, globalShortcut, screen } = require('electron')
const fs = require('fs')
const path = require('path')
const { uIOhook, UiohookKey } = require('uiohook-napi')
const WebSocket = require('ws')

const appDataRoot = path.join(__dirname, '.electron-data')

app.setPath('userData', path.join(appDataRoot, 'userData'))
if (typeof app.setPath === 'function') {
  app.setPath('sessionData', path.join(appDataRoot, 'sessionData'))
}
app.commandLine.appendSwitch('disable-gpu-shader-disk-cache')

const NOTCH_WIDTH = 560
const NOTCH_HEIGHT = 36
const WINDOWS_CORNER_CROP = 8
const HARNESS_UI_WS_URL = 'ws://127.0.0.1:8766'
const ALT_TRIPLE_WINDOW_MS = 600
const HOTKEY_DEBOUNCE_MS = 250

let mainWindow = null
let harnessWs = null
let harnessReconnectTimer = null
let alwaysOnMode = false
let currentState = 'IDLE'
let altPressCount = 0
let altPressTimer = null
let altDown = false
let lastHotkeyAt = 0

function getNotchBounds() {
  const primaryDisplay = screen.getPrimaryDisplay()
  const { x, width } = primaryDisplay.bounds

  return {
    width: NOTCH_WIDTH,
    height: NOTCH_HEIGHT + WINDOWS_CORNER_CROP,
    x: x + Math.floor((width - NOTCH_WIDTH) / 2),
    y: -WINDOWS_CORNER_CROP,
  }
}

function createNotchWindow() {
  const win = new BrowserWindow({
    ...getNotchBounds(),
    frame: false,
    transparent: false,
    backgroundColor: '#000000',
    alwaysOnTop: true,
    focusable: true,
    skipTaskbar: true,
    resizable: false,
    movable: false,
    minimizable: false,
    maximizable: false,
    fullscreenable: false,
    hasShadow: false,
    show: false,
    webPreferences: {
      preload: path.join(__dirname, 'preload.js'),
      contextIsolation: true,
      nodeIntegration: false,
    },
  })

  win.setAlwaysOnTop(true, 'screen-saver')
  win.setVisibleOnAllWorkspaces(true, { visibleOnFullScreen: true })
  win.setIgnoreMouseEvents(false)
  return win
}

function loadRenderer(win) {
  if (process.env.VITE_DEV_SERVER_URL) {
    win.loadURL(process.env.VITE_DEV_SERVER_URL)
    return
  }

  const rendererPath = path.join(__dirname, 'dist', 'renderer', 'index.html')
  if (fs.existsSync(rendererPath)) {
    win.loadFile(rendererPath)
    return
  }

  win.loadURL(
    'data:text/html;charset=utf-8,' +
      encodeURIComponent(`<!doctype html>
<html>
  <body style="margin:0;overflow:hidden;background:#000">
    <div style="
      width:560px;
      height:36px;
      display:flex;
      align-items:center;
      justify-content:center;
      color:white;
      font:12px system-ui, sans-serif;
      background:rgba(0,0,0,0.85);
      border-radius:0 0 12px 12px;
      box-sizing:border-box;
    ">
      Winky
    </div>
  </body>
</html>`)
  )
}

function showNotch() {
  if (!mainWindow || mainWindow.isDestroyed()) {
    return
  }

  const bounds = getNotchBounds()
  mainWindow.setBounds(bounds)
  mainWindow.setAlwaysOnTop(true, 'screen-saver')
  mainWindow.show()
  mainWindow.moveTop()
  console.log(`Notch shown ${JSON.stringify(bounds)} visible=${mainWindow.isVisible()}`)
}

function hideNotch() {
  if (!mainWindow || mainWindow.isDestroyed()) {
    return
  }

  mainWindow.hide()
  console.log(`Notch hidden visible=${mainWindow.isVisible()}`)
}

function handleHotkeyPress() {
  if (!mainWindow) {
    return
  }

  const now = Date.now()
  if (now - lastHotkeyAt < HOTKEY_DEBOUNCE_MS) {
    return
  }
  lastHotkeyAt = now

  console.log(`Alt+X hotkey received visible=${mainWindow.isVisible()} state=${currentState}`)
  if (!mainWindow.isVisible()) {
    showNotch()
    return
  }

  if (currentState === 'IDLE') {
    hideNotch()
  }
}

function registerGlobalShortcuts() {
  const registered = globalShortcut.register('Alt+X', () => {
    handleHotkeyPress()
  })

  console.log(`Alt+X registration: ${registered ? 'ok' : 'failed'}`)
}

function registerUiohookHotkeys() {
  uIOhook.on('keydown', (event) => {
    if (event.keycode === UiohookKey.X && altDown) {
      handleHotkeyPress()
      return
    }

    if (event.keycode !== UiohookKey.Alt && event.keycode !== UiohookKey.AltRight) {
      return
    }

    if (altDown) {
      return
    }

    altDown = true
    altPressCount += 1
    if (altPressTimer) {
      clearTimeout(altPressTimer)
    }

    altPressTimer = setTimeout(() => {
      altPressCount = 0
      altPressTimer = null
    }, ALT_TRIPLE_WINDOW_MS)

    if (altPressCount >= 3) {
      altPressCount = 0
      clearTimeout(altPressTimer)
      altPressTimer = null
      toggleAlwaysOnMode()
    }
  })

  uIOhook.on('keyup', (event) => {
    if (event.keycode === UiohookKey.Alt || event.keycode === UiohookKey.AltRight) {
      altDown = false
    }
  })

  uIOhook.start()
  console.log('uiohook started')
}

function toggleAlwaysOnMode() {
  alwaysOnMode = !alwaysOnMode
  console.log(`Always-on mode: ${alwaysOnMode ? 'on' : 'off'}`)

  if (alwaysOnMode) {
    showNotch()
    return
  }

  if (currentState === 'IDLE') {
    hideNotch()
  }
}

function connectHarnessWebSocket() {
  harnessWs = new WebSocket(HARNESS_UI_WS_URL)

  harnessWs.on('open', () => {
    console.log('Connected to harness UI WebSocket')
  })

  harnessWs.on('message', (data) => {
    let msg
    try {
      msg = JSON.parse(data.toString())
    } catch (error) {
      console.error('Invalid harness message', error)
      return
    }

    if (msg.type === 'state_change') {
      currentState = msg.state
    }

    if (mainWindow && !mainWindow.isDestroyed()) {
      mainWindow.webContents.send('harness-event', msg)
    }

    if (msg.type === 'dismiss') {
      setTimeout(() => {
        if (!alwaysOnMode) {
          hideNotch()
        }
      }, 800)
    }
  })

  harnessWs.on('close', scheduleHarnessReconnect)
  harnessWs.on('error', (error) => {
    console.error('Harness UI WebSocket error', error.message)
  })
}

function scheduleHarnessReconnect() {
  harnessWs = null
  if (harnessReconnectTimer) {
    return
  }

  harnessReconnectTimer = setTimeout(() => {
    harnessReconnectTimer = null
    connectHarnessWebSocket()
  }, 1000)
}

app.whenReady().then(() => {
  console.log('Electron ready')
  mainWindow = createNotchWindow()
  loadRenderer(mainWindow)

  mainWindow.webContents.on('did-finish-load', () => {
    console.log('Renderer loaded')
    showNotch()
  })

  mainWindow.webContents.on('did-fail-load', (_, code, description) => {
    console.error(`Renderer failed to load: ${code} ${description}`)
  })

  registerGlobalShortcuts()
  registerUiohookHotkeys()
  connectHarnessWebSocket()
})

app.on('will-quit', () => {
  globalShortcut.unregisterAll()

  if (altPressTimer) {
    clearTimeout(altPressTimer)
    altPressTimer = null
  }

  if (harnessReconnectTimer) {
    clearTimeout(harnessReconnectTimer)
    harnessReconnectTimer = null
  }

  if (harnessWs) {
    harnessWs.close()
    harnessWs = null
  }

  uIOhook.stop()
})
