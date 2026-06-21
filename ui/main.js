const { app, BrowserWindow, screen } = require('electron')
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

const NOTCH_WIDTH = 329
const NOTCH_HEIGHT = 44
const HARNESS_UI_WS_URL = 'ws://127.0.0.1:8766'
const ALT_TRIPLE_WINDOW_MS = 600

let mainWindow = null
let harnessWs = null
let harnessReconnectTimer = null
let alwaysOnMode = false
let currentState = 'IDLE'
let altPressCount = 0
let altPressTimer = null
let altDown = false
let xDown = false
let pushToTalkActive = false

function getNotchBounds() {
  const primaryDisplay = screen.getPrimaryDisplay()
  const { x, width } = primaryDisplay.bounds

  return {
    width: NOTCH_WIDTH,
    height: NOTCH_HEIGHT,
    x: x + Math.floor((width - NOTCH_WIDTH) / 2),
    y: 0,
  }
}

function createNotchWindow() {
  const win = new BrowserWindow({
    ...getNotchBounds(),
    frame: false,
    transparent: true,
    backgroundColor: '#00000000',
    alwaysOnTop: true,
    focusable: false,
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
  <body style="margin:0;overflow:hidden;background:transparent">
    <div style="
      width:329px;
      height:40px;
      display:flex;
      align-items:center;
      justify-content:center;
      color:#fff;
      font:500 12px 'Segoe UI', system-ui, sans-serif;
      background:#050505;
      border-radius:0 0 18px 18px;
      box-sizing:border-box;
    ">
      Always On
    </div>
  </body>
</html>`)
  )
}

function showNotch() {
  if (!mainWindow || mainWindow.isDestroyed()) {
    return
  }

  mainWindow.setBounds(getNotchBounds())
  mainWindow.setAlwaysOnTop(true, 'screen-saver')
  mainWindow.showInactive()
  mainWindow.moveTop()
}

function hideNotch() {
  if (!mainWindow || mainWindow.isDestroyed()) {
    return
  }

  mainWindow.hide()
}

function sendRendererEvent(msg) {
  if (!mainWindow || mainWindow.isDestroyed()) {
    return
  }

  mainWindow.webContents.send('harness-event', msg)
}

function sendToHarness(msg) {
  if (!harnessWs || harnessWs.readyState !== WebSocket.OPEN) {
    return
  }

  harnessWs.send(JSON.stringify(msg))
}

function setAlwaysOnMode(enabled) {
  alwaysOnMode = enabled
  sendRendererEvent({ type: 'mode_change', alwaysOnMode })

  if (alwaysOnMode) {
    showNotch()
    sendToHarness({ type: 'input_control', active: true, mode: 'always_on' })
    return
  }

  sendToHarness({ type: 'input_control', active: false, submit: false })
  if (!pushToTalkActive && currentState !== 'THINKING' && currentState !== 'SPEAKING') {
    hideNotch()
  }
}

function toggleAlwaysOnMode() {
  setAlwaysOnMode(!alwaysOnMode)
}

function beginPushToTalk() {
  if (pushToTalkActive || alwaysOnMode) {
    return
  }

  pushToTalkActive = true
  showNotch()
  sendRendererEvent({ type: 'push_to_talk', active: true })
  sendToHarness({ type: 'input_control', active: true, mode: 'push_to_talk' })
}

function endPushToTalk() {
  if (!pushToTalkActive) {
    return
  }

  pushToTalkActive = false
  sendRendererEvent({ type: 'push_to_talk', active: false })
  sendToHarness({ type: 'input_control', active: false, submit: true })

  if (currentState === 'IDLE' || currentState === 'LISTENING') {
    setTimeout(() => {
      if (!alwaysOnMode && !pushToTalkActive && currentState !== 'THINKING' && currentState !== 'SPEAKING') {
        hideNotch()
      }
    }, 120)
  }
}

function registerUiohookHotkeys() {
  uIOhook.on('keydown', (event) => {
    if (event.keycode === UiohookKey.X) {
      xDown = true
      if (altDown) {
        beginPushToTalk()
      }
      return
    }

    if (event.keycode !== UiohookKey.Alt && event.keycode !== UiohookKey.AltRight) {
      return
    }

    if (!altDown) {
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
    }

    altDown = true
    if (xDown) {
      beginPushToTalk()
    }
  })

  uIOhook.on('keyup', (event) => {
    if (event.keycode === UiohookKey.X) {
      xDown = false
      endPushToTalk()
      return
    }

    if (event.keycode === UiohookKey.Alt || event.keycode === UiohookKey.AltRight) {
      altDown = false
      endPushToTalk()
    }
  })

  uIOhook.start()
  console.log('uiohook started')
}

function connectHarnessWebSocket() {
  harnessWs = new WebSocket(HARNESS_UI_WS_URL)

  harnessWs.on('open', () => {
    console.log('Connected to harness UI WebSocket')
    sendToHarness({
      type: 'input_control',
      active: alwaysOnMode || pushToTalkActive,
      submit: false,
      mode: alwaysOnMode ? 'always_on' : pushToTalkActive ? 'push_to_talk' : undefined,
    })
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

    sendRendererEvent(msg)

    if (msg.type === 'dismiss') {
      setTimeout(() => {
        if (!alwaysOnMode && !pushToTalkActive) {
          hideNotch()
        }
      }, 180)
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
    sendRendererEvent({ type: 'mode_change', alwaysOnMode })
  })

  mainWindow.webContents.on('did-fail-load', (_, code, description) => {
    console.error(`Renderer failed to load: ${code} ${description}`)
  })

  registerUiohookHotkeys()
  connectHarnessWebSocket()
})

app.on('will-quit', () => {
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
