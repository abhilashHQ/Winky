const { contextBridge, ipcRenderer } = require('electron')

contextBridge.exposeInMainWorld('winky', {
  onHarnessEvent: (cb) => {
    if (typeof cb !== 'function') {
      return () => {}
    }

    const listener = (_, msg) => cb(msg)
    ipcRenderer.on('harness-event', listener)

    return () => {
      ipcRenderer.removeListener('harness-event', listener)
    }
  },
  onCanvasEvent: (cb) => {
    if (typeof cb !== 'function') {
      return () => {}
    }

    const listener = (_, msg) => cb(msg)
    ipcRenderer.on('canvas-event', listener)

    return () => {
      ipcRenderer.removeListener('canvas-event', listener)
    }
  },
  sendToHarness: (msg) => ipcRenderer.invoke('send-to-harness', msg),
  resizeApproval: (height) => ipcRenderer.invoke('resize-approval', height),
  canvasCleared: () => ipcRenderer.invoke('canvas-cleared')
})
